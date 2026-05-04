"""Anthropic Claude API 呼び出しの薄いラッパー。

- 議案書テキスト → 中間サマリ JSON
- 中間サマリ + チャネル指定 → 広報文
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
CONFIG_DIR = ROOT / "config"


# モデル定義: 表示名 → API ID + 振る舞い
MODELS: dict[str, dict[str, Any]] = {
    "Opus 4.7（最高品質・高コスト）": {
        "id": "claude-opus-4-7",
        "thinking": {"type": "adaptive"},
        "effort": "high",
    },
    "Sonnet 4.6（バランス）": {
        "id": "claude-sonnet-4-6",
        "thinking": {"type": "adaptive"},
        "effort": "medium",
    },
    "Haiku 4.5（高速・低コスト）": {
        "id": "claude-haiku-4-5",
        "thinking": None,
        "effort": None,
    },
}
DEFAULT_MODEL_LABEL = "Haiku 4.5（高速・低コスト）"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(_read(path))


@dataclass
class Configs:
    lom: dict[str, Any]
    jc_style: dict[str, Any]
    channels: dict[str, Any]

    @classmethod
    def load(cls) -> "Configs":
        return cls(
            lom=_load_yaml(CONFIG_DIR / "lom.yaml"),
            jc_style=_load_yaml(CONFIG_DIR / "jc_style.yaml"),
            channels=_load_yaml(CONFIG_DIR / "channels.yaml"),
        )

    def channel(self, channel_id: str) -> dict[str, Any]:
        return self.channels["channels"][channel_id]


def _build_request_kwargs(
    *,
    model_label: str,
    system_blocks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> dict[str, Any]:
    spec = MODELS[model_label]
    kwargs: dict[str, Any] = {
        "model": spec["id"],
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": messages,
    }
    if spec.get("thinking"):
        kwargs["thinking"] = spec["thinking"]
    if spec.get("effort"):
        kwargs["output_config"] = {"effort": spec["effort"]}
    return kwargs


def _stream_text(client: anthropic.Anthropic, **kwargs: Any) -> tuple[str, Any]:
    """ストリーミングで応答を取得し、テキストと最終メッセージを返す。"""
    with client.messages.stream(**kwargs) as stream:
        message = stream.get_final_message()
    text_parts = [b.text for b in message.content if b.type == "text"]
    return "".join(text_parts).strip(), message


def extract_summary(
    client: anthropic.Anthropic,
    document_text: str,
    model_label: str = DEFAULT_MODEL_LABEL,
) -> tuple[dict[str, Any], Any]:
    """議案書テキストから中間サマリ JSON を取り出す。"""
    system_prompt = _read(PROMPTS_DIR / "extract.md")
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages = [{"role": "user", "content": f"# 議案書本文\n\n{document_text}"}]

    kwargs = _build_request_kwargs(
        model_label=model_label,
        system_blocks=system_blocks,
        messages=messages,
        max_tokens=4000,
    )
    text, message = _stream_text(client, **kwargs)
    summary = _parse_json_response(text)
    return summary, message


def generate_channel(
    client: anthropic.Anthropic,
    summary: dict[str, Any],
    channel_id: str,
    configs: Configs,
    model_label: str = DEFAULT_MODEL_LABEL,
    extra_instructions: str | None = None,
) -> tuple[str, Any]:
    """中間サマリとチャネル名から、当該チャネル向けの広報文を生成する。"""
    system_blocks = _build_channel_system(channel_id, configs)
    user_payload = _build_user_payload(summary, channel_id, configs, extra_instructions)
    messages = [{"role": "user", "content": user_payload}]

    max_tokens = _max_tokens_for_channel(channel_id)

    kwargs = _build_request_kwargs(
        model_label=model_label,
        system_blocks=system_blocks,
        messages=messages,
        max_tokens=max_tokens,
    )
    text, message = _stream_text(client, **kwargs)
    return text, message


def revise_channel(
    client: anthropic.Anthropic,
    current_text: str,
    instruction: str,
    channel_id: str,
    configs: Configs,
    model_label: str = DEFAULT_MODEL_LABEL,
) -> tuple[str, Any]:
    """既存の広報文に修正指示を反映して書き直す。"""
    system_blocks = _build_channel_system(channel_id, configs)
    user_payload = (
        "# 現在の広報文\n\n"
        f"{current_text}\n\n"
        "# 修正指示（最優先で反映）\n\n"
        f"{instruction}\n\n"
        "上記の修正指示を反映して、広報文を書き直してください。\n"
        "チャネル規約・スタイルガイド・LOM情報は引き続き厳守してください。\n"
        "出力は書き直した本文のみ（前置き・解説不要）。"
    )
    messages = [{"role": "user", "content": user_payload}]

    max_tokens = _max_tokens_for_channel(channel_id)
    kwargs = _build_request_kwargs(
        model_label=model_label,
        system_blocks=system_blocks,
        messages=messages,
        max_tokens=max_tokens,
    )
    text, message = _stream_text(client, **kwargs)
    return text, message


def _build_channel_system(channel_id: str, configs: Configs) -> list[dict[str, Any]]:
    base = _read(PROMPTS_DIR / "system_base.md")
    channel_prompt = _read(PROMPTS_DIR / f"{channel_id}.md")

    lom_block = (
        "# LOM情報\n\n"
        f"```yaml\n{yaml.safe_dump(configs.lom, allow_unicode=True, sort_keys=False)}```"
    )
    style_block = (
        "# JC広報スタイルガイド\n\n"
        f"```yaml\n{yaml.safe_dump(configs.jc_style, allow_unicode=True, sort_keys=False)}```"
    )
    channel_rule = configs.channel(channel_id)
    channel_rule_block = (
        f"# チャネル規約: {channel_rule['label']}\n\n"
        f"```yaml\n{yaml.safe_dump(channel_rule, allow_unicode=True, sort_keys=False)}```"
    )

    text = "\n\n".join([base, lom_block, style_block, channel_rule_block, channel_prompt])
    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _build_user_payload(
    summary: dict[str, Any],
    channel_id: str,
    configs: Configs,
    extra_instructions: str | None,
) -> str:
    summary_json = json.dumps(summary, ensure_ascii=False, indent=2)
    parts = [
        "# 中間サマリ",
        "",
        "```json",
        summary_json,
        "```",
        "",
        f"以上のサマリをもとに、`{channel_id}` チャネル向けの広報文を1本生成してください。",
    ]
    if extra_instructions:
        parts.extend(["", "# 追加指示（最優先で反映）", "", extra_instructions])
    return "\n".join(parts)


def _max_tokens_for_channel(channel_id: str) -> int:
    return {
        "line": 1500,
        "x": 3000,  # 5本セットなので長めに確保
        "facebook": 2500,
        "instagram": 2500,
        "website": 4000,
    }.get(channel_id, 2500)


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_json_response(text: str) -> dict[str, Any]:
    """応答からJSONを抜き出してパースする。前後にコードブロックや解説が混じっても拾えるように。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = _JSON_RE.search(stripped)
        if not match:
            raise
        return json.loads(match.group(0))
