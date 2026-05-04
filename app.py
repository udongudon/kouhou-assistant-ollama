"""いわきJC 広報アシスタント Streamlit アプリ。

議案書(PDF/Word/貼り付け) → 中間サマリ → 5チャネルの広報文を生成する。
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Any

import anthropic
import streamlit as st
from dotenv import load_dotenv

from src.extract import extract, truncate_for_prompt
from src.generate import (
    DEFAULT_MODEL_LABEL,
    MODELS,
    Configs,
    extract_summary,
    generate_channel,
    revise_channel,
)

load_dotenv()

st.set_page_config(
    page_title="いわきJC 広報アシスタント",
    page_icon="📣",
    layout="wide",
)


def _get_secret(name: str) -> str | None:
    if name in os.environ:
        return os.environ[name]
    try:
        return st.secrets.get(name)  # type: ignore[attr-defined]
    except (FileNotFoundError, KeyError, AttributeError):
        return None


def _check_password() -> bool:
    password = _get_secret("APP_PASSWORD")
    if not password:
        return True

    if st.session_state.get("authed"):
        return True

    st.title("いわきJC 広報アシスタント")
    entered = st.text_input("パスワード", type="password")
    if st.button("ログイン"):
        if entered == password:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")
    return False


def _get_client() -> anthropic.Anthropic | None:
    api_key = _get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        st.error(
            "ANTHROPIC_API_KEY が設定されていません。`.env` か `.streamlit/secrets.toml` に追加してください。"
        )
        return None
    return anthropic.Anthropic(api_key=api_key)


def _input_section() -> str | None:
    """議案書テキストを取得。"""
    st.subheader("1. 議案書を入力")
    tab_paste, tab_upload = st.tabs(["テキストを貼り付け", "ファイルをアップロード"])

    with tab_paste:
        pasted = st.text_area(
            "議案書の本文を貼り付けてください",
            height=300,
            key="pasted_text",
            placeholder="第○回通常総会 第○号議案 ○○事業（案）...",
        )
        if pasted.strip():
            return pasted.strip()

    with tab_upload:
        uploaded = st.file_uploader(
            "PDF / Word(.docx) / テキスト(.txt) を選択",
            type=["pdf", "docx", "txt", "md"],
            accept_multiple_files=False,
        )
        if uploaded is not None:
            try:
                file_bytes = uploaded.read()
                text = extract(io.BytesIO(file_bytes), uploaded.name)
                if not text.strip():
                    st.warning("ファイルからテキストを抽出できませんでした。スキャンPDFの可能性があります。")
                    return None
                return text
            except Exception as exc:  # noqa: BLE001
                st.error(f"ファイルの読み込みに失敗しました: {exc}")
                return None
    return None


def _channel_selection() -> list[str]:
    st.subheader("2. 生成するチャネルを選ぶ")
    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown("**対内（会員向け）**")
        gen_line = st.checkbox("LINE（会員グループ）", value=True, key="ch_line")
    with col2:
        st.markdown("**対外（一般向け）**")
        c1, c2, c3, c4 = st.columns(4)
        gen_x = c1.checkbox("X", value=True, key="ch_x")
        gen_fb = c2.checkbox("Facebook", value=True, key="ch_fb")
        gen_ig = c3.checkbox("Instagram", value=True, key="ch_ig")
        gen_hp = c4.checkbox("HP", value=True, key="ch_hp")

    selected: list[str] = []
    if gen_line:
        selected.append("line")
    if gen_x:
        selected.append("x")
    if gen_fb:
        selected.append("facebook")
    if gen_ig:
        selected.append("instagram")
    if gen_hp:
        selected.append("website")
    return selected


def _options_section() -> tuple[str, str]:
    with st.expander("オプション（モデル・追加指示）", expanded=False):
        model_label = st.selectbox(
            "使用モデル",
            options=list(MODELS.keys()),
            index=list(MODELS.keys()).index(DEFAULT_MODEL_LABEL),
            help="高品質な順: Opus 4.7 > Sonnet 4.6 > Haiku 4.5。コスト感もこの順。",
        )
        extra = st.text_area(
            "追加指示（任意）",
            placeholder="例: 「子育て世代に響く言葉づかいで」「申込URL https://example.com/apply を必ず入れる」",
            height=100,
        )
    return model_label, extra.strip()


def _ensure_summary(
    client: anthropic.Anthropic,
    document_text: str,
    model_label: str,
) -> dict[str, Any] | None:
    """サマリをセッションに作る or 取り出す。"""
    cache_key = "summary"
    text_hash = hash((document_text, model_label))

    if (
        cache_key in st.session_state
        and st.session_state.get("summary_hash") == text_hash
    ):
        return st.session_state[cache_key]

    with st.status("議案書を解析中（中間サマリを生成）...", expanded=False) as status:
        try:
            summary, _ = extract_summary(client, document_text, model_label=model_label)
            status.update(label="議案書の解析が完了しました", state="complete")
        except json.JSONDecodeError as exc:
            status.update(label="サマリのJSON解析に失敗", state="error")
            st.error(f"サマリのJSONを解析できませんでした: {exc}")
            return None
        except anthropic.APIError as exc:
            status.update(label="API呼び出しに失敗", state="error")
            st.error(f"Claude API エラー: {exc}")
            return None

    st.session_state[cache_key] = summary
    st.session_state["summary_hash"] = text_hash
    return summary


def _set_result(channel_id: str, text: str) -> None:
    """生成結果を更新し、編集中のテキストエリアもリセットする。"""
    st.session_state[f"result_{channel_id}"] = text
    st.session_state.pop(f"editor_{channel_id}", None)


def _push_history(channel_id: str, text: str, instruction: str) -> None:
    """修正前のテキストを履歴に積む。"""
    history_key = f"history_{channel_id}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []
    st.session_state[history_key].append(
        {"text": text, "next_instruction": instruction}
    )


def _run_channel(
    client: anthropic.Anthropic,
    summary: dict[str, Any],
    channel_id: str,
    configs: Configs,
    model_label: str,
    extra_instructions: str,
) -> str | None:
    spinner_label = f"{configs.channel(channel_id)['label']} を生成中..."
    with st.spinner(spinner_label):
        try:
            text, _ = generate_channel(
                client,
                summary,
                channel_id,
                configs,
                model_label=model_label,
                extra_instructions=extra_instructions or None,
            )
        except anthropic.APIError as exc:
            st.error(f"生成に失敗しました ({channel_id}): {exc}")
            return None
    _set_result(channel_id, text)
    return text


_X_POST_RE = re.compile(r"【([^】]+)】\s*\n([\s\S]*?)(?=\n【|\Z)")


def _parse_x_posts(text: str) -> list[tuple[str, str]]:
    return [
        (m.group(1).strip(), m.group(2).strip())
        for m in _X_POST_RE.finditer(text)
    ]


def _render_char_counts(channel_id: str, text: str, channel_cfg: dict[str, Any]) -> None:
    target = channel_cfg.get("char_target", "")
    total = len(text)

    if channel_id == "x":
        posts = _parse_x_posts(text)
        if posts:
            cols = st.columns(len(posts))
            for col, (label, body) in zip(cols, posts):
                # ハッシュタグ・URLを除いた本文の概算文字数
                body_no_url = re.sub(r"https?://\S+", "URL", body)
                body_no_tags = re.sub(r"#\S+", "", body_no_url).strip()
                body_len = len(body_no_tags)
                warning = "⚠️" if body_len > 140 else ""
                col.metric(f"{label}", f"{body_len}字 {warning}", help="本文のみ（URL/ハッシュタグ除く目安）")
            st.caption(f"📝 全体: {total}字　目安: {target}")
            return

    # その他のチャネルは合計のみ
    st.caption(f"📝 文字数: **{total}字**　目安: {target}")


def _render_revision_form(
    client: anthropic.Anthropic,
    channel_id: str,
    current_text: str,
    configs: Configs,
    model_label: str,
) -> None:
    """「ここをこう直して」フォーム。"""
    label = configs.channel(channel_id)["label"]
    with st.form(key=f"revise_form_{channel_id}", clear_on_submit=True):
        instruction = st.text_input(
            "ここをこう直して",
            placeholder=f"例: もっと熱量を上げて／子育て世代向けに／申込URL https://... を入れて／敬語を一段階柔らかく",
            key=f"revise_input_{channel_id}",
        )
        submitted = st.form_submit_button(f"指示を反映して書き直す")

    if submitted and instruction.strip():
        with st.spinner(f"{label} を書き直し中..."):
            try:
                new_text, _ = revise_channel(
                    client,
                    current_text=current_text,
                    instruction=instruction.strip(),
                    channel_id=channel_id,
                    configs=configs,
                    model_label=model_label,
                )
            except anthropic.APIError as exc:
                st.error(f"書き直しに失敗しました: {exc}")
                return
        _push_history(channel_id, current_text, instruction.strip())
        _set_result(channel_id, new_text)
        st.rerun()


def _render_history(channel_id: str) -> None:
    history = st.session_state.get(f"history_{channel_id}", [])
    if not history:
        return
    with st.expander(f"修正履歴（{len(history)}件）— 過去版に戻せます", expanded=False):
        for i, h in enumerate(reversed(history)):
            version_num = len(history) - i
            st.markdown(
                f"**版 {version_num}** — 次の指示: 『{h['next_instruction']}』"
            )
            st.text(h["text"])
            if st.button("この版に戻す", key=f"restore_{channel_id}_{version_num}"):
                _set_result(channel_id, h["text"])
                st.rerun()
            st.divider()


def _render_results(
    client: anthropic.Anthropic,
    summary: dict[str, Any],
    selected_channels: list[str],
    configs: Configs,
    model_label: str,
    extra_instructions: str,
) -> None:
    st.subheader("3. 生成された広報文")
    with st.expander("中間サマリを確認", expanded=False):
        st.json(summary)

    if not selected_channels:
        st.info("生成するチャネルを選択してください。")
        return

    tab_labels = [configs.channel(cid)["label"] for cid in selected_channels]
    tabs = st.tabs(tab_labels)

    for tab, channel_id in zip(tabs, selected_channels):
        with tab:
            cache_key = f"result_{channel_id}"
            channel_cfg = configs.channel(channel_id)

            # 初回はAPIを叩いて生成
            if cache_key not in st.session_state:
                _run_channel(
                    client, summary, channel_id, configs, model_label, extra_instructions
                )

            text = st.session_state.get(cache_key)
            if not text:
                continue

            # 文字数表示
            _render_char_counts(channel_id, text, channel_cfg)

            # 編集可能テキストエリア（編集即反映）
            editor_key = f"editor_{channel_id}"
            edited = st.text_area(
                "本文（直接編集できます）",
                value=text,
                height=320,
                key=editor_key,
                label_visibility="collapsed",
            )
            if edited != text:
                # 編集された内容を反映（履歴は積まない: 手作業の小修正用）
                st.session_state[cache_key] = edited
                text = edited

            # 「ここをこう直して」フォーム
            _render_revision_form(client, channel_id, text, configs, model_label)

            # ボタン群
            col_a, col_b, col_c, _ = st.columns([1, 1, 1, 3])
            with col_a:
                if st.button("白紙から再生成", key=f"btn_regen_{channel_id}",
                             help="議案書から作り直します（編集や修正履歴は破棄）"):
                    st.session_state.pop(cache_key, None)
                    st.session_state.pop(editor_key, None)
                    st.session_state.pop(f"history_{channel_id}", None)
                    st.rerun()
            with col_b:
                filename_stem = (
                    summary.get("short_title")
                    or summary.get("title")
                    or "output"
                )
                st.download_button(
                    "DL .txt",
                    data=text,
                    file_name=f"{channel_id}_{filename_stem}.txt",
                    mime="text/plain",
                    key=f"btn_dl_{channel_id}",
                )
            with col_c:
                if st.session_state.get(f"history_{channel_id}"):
                    if st.button("履歴クリア", key=f"btn_clear_hist_{channel_id}"):
                        st.session_state.pop(f"history_{channel_id}", None)
                        st.rerun()

            # 修正履歴
            _render_history(channel_id)


def main() -> None:
    if not _check_password():
        return

    st.title("📣 いわきJC 広報アシスタント")
    st.caption("議案書から LINE / X / Facebook / Instagram / HP の広報文を生成します。")

    client = _get_client()
    if client is None:
        return

    document_text = _input_section()
    selected_channels = _channel_selection()
    model_label, extra_instructions = _options_section()

    st.divider()

    do_generate = st.button(
        "広報文を生成 / 再構築",
        type="primary",
        disabled=not document_text,
        help="クリックすると中間サマリと選択チャネルの広報文をすべて生成し直します。",
    )

    if do_generate:
        # 既存結果・編集中テキスト・修正履歴をすべてリセット
        for key in list(st.session_state.keys()):
            if key.startswith(("result_", "editor_", "history_")) or key in (
                "summary",
                "summary_hash",
            ):
                st.session_state.pop(key, None)

    if document_text and (do_generate or "summary" in st.session_state):
        truncated, was_truncated = truncate_for_prompt(document_text)
        if was_truncated:
            st.warning(
                f"議案書が長いため、先頭から約3万字に切り詰めて処理します（元: {len(document_text):,}字）。"
            )

        summary = _ensure_summary(client, truncated, model_label)
        if summary is None:
            return

        configs = Configs.load()
        _render_results(
            client=client,
            summary=summary,
            selected_channels=selected_channels,
            configs=configs,
            model_label=model_label,
            extra_instructions=extra_instructions,
        )


if __name__ == "__main__":
    main()
