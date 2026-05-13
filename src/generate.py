"""Ollama Cloud API 呼び出しの薄いラッパー。

- 議案書テキスト → 中間サマリ JSON
- 中間サマリ + チャネル指定 → 広報文
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import ollama
import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
CONFIG_DIR = ROOT / "config"
OLLAMA_CLOUD_HOST = "https://ollama.com"

OllamaClient = ollama.Client
OllamaApiError = (ollama.ResponseError, httpx.HTTPError)


# モデル定義: 表示名 → Ollama Cloud model ID + 振る舞い
MODELS: dict[str, dict[str, Any]] = {
    "GPT-OSS 20B（無料枠・高速）": {
        "id": "gpt-oss:20b",
        "think": "low",
        "temperature": 0.7,
    },
    "GPT-OSS 120B（無料枠・高品質）": {
        "id": "gpt-oss:120b",
        "think": "medium",
        "temperature": 0.7,
    },
    "Gemma 4 31B Cloud（自然文・構造化）": {
        "id": "gemma4:31b-cloud",
        "think": "medium",
        "temperature": 0.7,
    },
}
DEFAULT_MODEL_LABEL = "GPT-OSS 20B（無料枠・高速）"


def create_client(api_key: str) -> OllamaClient:
    """UI層がOllama SDKへ直接依存しないようにクライアント生成を集約する。"""
    return ollama.Client(
        host=OLLAMA_CLOUD_HOST,
        headers={"Authorization": f"Bearer {api_key}"},
    )


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
    system_text: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    response_format: str | None = None,
) -> dict[str, Any]:
    spec = MODELS[model_label]
    chat_messages = [{"role": "system", "content": system_text}, *messages]
    options: dict[str, Any] = {
        "num_predict": max_tokens,
        "temperature": spec["temperature"],
    }
    kwargs: dict[str, Any] = {
        "model": spec["id"],
        "messages": chat_messages,
        "stream": True,
        "options": options,
        "think": spec["think"],
    }
    if response_format:
        kwargs["format"] = response_format
    return kwargs


def _stream_text(client: OllamaClient, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    """ストリーミングで応答を受け取り、テキストと最終チャンクを返す。"""
    text_parts: list[str] = []
    final_part: dict[str, Any] = {}

    for part in client.chat(**kwargs):
        final_part = _to_dict(part)
        message = _get_value(part, "message", {})
        content = _get_value(message, "content", "")
        if content:
            text_parts.append(content)

    return "".join(text_parts).strip(), final_part


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, dict):
            return dumped
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _get_value(value: Any, key: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def extract_summary(
    client: OllamaClient,
    document_text: str,
    model_label: str = DEFAULT_MODEL_LABEL,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """議案書テキストから中間サマリ JSON を取り出す。"""
    system_prompt = _read(PROMPTS_DIR / "extract.md")
    messages = [{"role": "user", "content": f"# 議案書本文\n\n{document_text}"}]

    kwargs = _build_request_kwargs(
        model_label=model_label,
        system_text=system_prompt,
        messages=messages,
        max_tokens=4000,
        response_format="json",
    )
    text, message = _stream_text(client, **kwargs)
    summary = _parse_json_response(text)
    return summary, message


def generate_channel(
    client: OllamaClient,
    summary: dict[str, Any],
    channel_id: str,
    configs: Configs,
    model_label: str = DEFAULT_MODEL_LABEL,
    extra_instructions: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """中間サマリとチャネル名から、当該チャネル向けの広報文を生成する。"""
    system_text = _build_channel_system(channel_id, configs)
    user_payload = _build_user_payload(summary, channel_id, configs, extra_instructions)
    messages = [{"role": "user", "content": user_payload}]

    max_tokens = _max_tokens_for_channel(channel_id)

    kwargs = _build_request_kwargs(
        model_label=model_label,
        system_text=system_text,
        messages=messages,
        max_tokens=max_tokens,
    )
    text, message = _stream_text(client, **kwargs)
    if channel_id == "x" and _x_post_count(text) < len(X_POST_LABELS):
        text, message = _repair_x_post_set(
            client=client,
            system_text=system_text,
            user_payload=user_payload,
            first_text=text,
            model_label=model_label,
            max_tokens=max_tokens,
        )
    return text, message


def revise_channel(
    client: OllamaClient,
    current_text: str,
    instruction: str,
    channel_id: str,
    configs: Configs,
    model_label: str = DEFAULT_MODEL_LABEL,
) -> tuple[str, dict[str, Any]]:
    """既存の広報文に修正指示を反映して書き直す。"""
    system_text = _build_channel_system(channel_id, configs)
    user_payload = (
        "# 現在の広報文\n\n"
        f"{current_text}\n\n"
        "# 修正指示（最優先で反映）\n\n"
        f"{instruction}\n\n"
        "上記の修正指示を反映して、広報文を書き直してください。\n"
        "チャネル要件・スタイルガイド・LOM情報は引き続き厳守してください。\n"
        "出力は書き直した本文のみ（前置きや説明不要）。"
    )
    messages = [{"role": "user", "content": user_payload}]

    max_tokens = _max_tokens_for_channel(channel_id)
    kwargs = _build_request_kwargs(
        model_label=model_label,
        system_text=system_text,
        messages=messages,
        max_tokens=max_tokens,
    )
    text, message = _stream_text(client, **kwargs)
    return text, message


def _build_channel_system(channel_id: str, configs: Configs) -> str:
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
        f"# チャネル要件: {channel_rule['label']}\n\n"
        f"```yaml\n{yaml.safe_dump(channel_rule, allow_unicode=True, sort_keys=False)}```"
    )

    return "\n\n".join([base, lom_block, style_block, channel_rule_block, channel_prompt])


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
        "line": 2600,
        "x": 3000,  # 5本セットなので長めに確保
        "facebook": 2500,
        "instagram": 2500,
        "website": 4000,
    }.get(channel_id, 2500)


X_POST_LABELS = ("告知", "1週間前", "前日", "当日", "事後報告")
_X_POST_RE = re.compile(r"【([^】]+)】\s*\n([\s\S]*?)(?=\n【|\Z)")


def _x_post_count(text: str) -> int:
    """モデルがXの5本セット形式を守ったかを確認する。"""
    labels = {match.group(1).strip() for match in _X_POST_RE.finditer(text)}
    return sum(1 for label in X_POST_LABELS if label in labels)


def _repair_x_post_set(
    *,
    client: OllamaClient,
    system_text: str,
    user_payload: str,
    first_text: str,
    model_label: str,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """X出力が1本だけになった場合に、5本セットへ作り直す。"""
    labels = "、".join(f"【{label}】" for label in X_POST_LABELS)
    repair_payload = (
        f"{user_payload}\n\n"
        "# 重要な再生成条件\n\n"
        f"直前の出力はX投稿セットとして不完全でした。必ず {labels} の5見出しをこの順番で出力してください。\n"
        "見出しの表記は完全一致にしてください。1本だけで終わらせず、5本すべてを書いてください。\n"
        "各投稿は本文140字以内を目安にし、コードブロックや前置きは出力しないでください。\n\n"
        "# 直前の不完全な出力\n\n"
        f"{first_text}"
    )
    kwargs = _build_request_kwargs(
        model_label=model_label,
        system_text=system_text,
        messages=[{"role": "user", "content": repair_payload}],
        max_tokens=max_tokens,
    )
    return _stream_text(client, **kwargs)


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _parse_json_response(text: str) -> dict[str, Any]:
    """応答からJSONを柔軟に取り出してパースする。"""
    stripped = text.strip()
    candidates = [stripped]
    candidates.extend(match.group(1).strip() for match in _FENCED_JSON_RE.finditer(stripped))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return json.loads(stripped)
