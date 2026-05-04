"""いわきJC 広報アシスタント Streamlit アプリ。

議案書(PDF/Word/貼り付け) → 中間サマリ → 5チャネルの広報文を生成する。
"""
from __future__ import annotations

import io
import json
import os
import re
from hashlib import sha256
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from src.extract import extract, truncate_for_prompt
from src.generate import (
    DEFAULT_MODEL_LABEL,
    MODELS,
    ClaudeApiError,
    ClaudeClient,
    Configs,
    create_client,
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


def _inject_theme_css() -> None:
    """Streamlit標準UIに公的な広報ツールらしい質感を足す。"""
    st.markdown(
        """
        <style>
        :root {
            --kouhou-navy: #102a43;
            --kouhou-blue: #1d5f9f;
            --kouhou-gold: #c79a35;
            --kouhou-ink: #1f2933;
            --kouhou-muted: #64748b;
            --kouhou-bg: #f4f7fb;
            --kouhou-card: rgba(255, 255, 255, 0.94);
            --kouhou-border: rgba(16, 42, 67, 0.12);
        }

        .stApp {
            background:
                radial-gradient(circle at top left, rgba(29, 95, 159, 0.14), transparent 34rem),
                linear-gradient(180deg, #f7fbff 0%, var(--kouhou-bg) 46%, #eef3f8 100%);
            color: var(--kouhou-ink);
        }

        .stApp,
        .stApp p,
        .stApp label,
        .stMarkdown,
        [data-testid="stMarkdownContainer"] {
            color: var(--kouhou-ink);
        }

        .block-container {
            max-width: 1180px;
            padding-top: 2.4rem;
            padding-bottom: 4rem;
        }

        .kouhou-hero {
            position: relative;
            overflow: hidden;
            border: 1px solid var(--kouhou-border);
            border-radius: 28px;
            padding: 2rem 2.2rem;
            margin-bottom: 1.4rem;
            background:
                linear-gradient(135deg, rgba(16, 42, 67, 0.96), rgba(29, 95, 159, 0.88)),
                radial-gradient(circle at 85% 15%, rgba(199, 154, 53, 0.32), transparent 18rem);
            box-shadow: 0 22px 55px rgba(16, 42, 67, 0.16);
            color: white;
        }

        .kouhou-hero,
        .kouhou-hero div,
        .kouhou-hero h1,
        .kouhou-hero p,
        .kouhou-hero span,
        .kouhou-hero strong {
            color: white;
        }

        .kouhou-hero .kouhou-kicker {
            color: #f8df9a;
        }

        .kouhou-hero::after {
            content: "";
            position: absolute;
            right: -5rem;
            bottom: -7rem;
            width: 18rem;
            height: 18rem;
            border-radius: 999px;
            border: 1px solid rgba(255, 255, 255, 0.18);
            background: rgba(255, 255, 255, 0.05);
        }

        .kouhou-kicker {
            display: inline-flex;
            gap: 0.45rem;
            align-items: center;
            margin-bottom: 0.75rem;
            color: #f8df9a;
            font-size: 0.82rem;
            font-weight: 700;
            letter-spacing: 0.16em;
            text-transform: uppercase;
        }

        .kouhou-hero h1 {
            margin: 0;
            font-size: clamp(2rem, 4vw, 3.7rem);
            line-height: 1.02;
            letter-spacing: -0.055em;
        }

        .kouhou-hero p {
            max-width: 48rem;
            margin: 1rem 0 0;
            color: rgba(255, 255, 255, 0.84);
            font-size: 1.05rem;
            line-height: 1.8;
        }

        .kouhou-steps {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.75rem;
            margin: 1.5rem 0 0;
            max-width: 54rem;
        }

        .kouhou-step {
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 18px;
            padding: 0.85rem 1rem;
            background: rgba(255, 255, 255, 0.09);
            backdrop-filter: blur(10px);
        }

        .kouhou-step strong {
            display: block;
            color: #fff8df;
            font-size: 0.92rem;
            margin-bottom: 0.2rem;
        }

        .kouhou-step span {
            color: rgba(255, 255, 255, 0.74);
            font-size: 0.82rem;
        }

        div[data-testid="stVerticalBlock"] > div:has(> .kouhou-card-start) {
            border: 1px solid var(--kouhou-border);
            border-radius: 24px;
            padding: 1.35rem 1.45rem 1.45rem;
            margin: 1rem 0;
            background: var(--kouhou-card);
            box-shadow: 0 14px 36px rgba(16, 42, 67, 0.08);
        }

        .kouhou-card-start {
            display: none;
        }

        .kouhou-section-label {
            display: inline-flex;
            align-items: center;
            gap: 0.55rem;
            margin-bottom: 0.35rem;
            color: var(--kouhou-blue);
            font-size: 0.82rem;
            font-weight: 800;
            letter-spacing: 0.08em;
        }

        .kouhou-section-title {
            margin: 0 0 0.2rem;
            color: var(--kouhou-navy);
            font-size: 1.42rem;
            font-weight: 800;
            letter-spacing: -0.025em;
        }

        .kouhou-section-caption {
            margin: 0 0 1rem;
            color: var(--kouhou-muted);
            font-size: 0.93rem;
        }

        .kouhou-channel-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.65rem;
            margin-bottom: 0.8rem;
        }

        .kouhou-channel-card {
            min-height: 6.4rem;
            border: 1px solid var(--kouhou-border);
            border-radius: 18px;
            padding: 0.9rem;
            background: linear-gradient(180deg, #ffffff, #f8fbff);
        }

        .kouhou-channel-card strong {
            display: block;
            color: var(--kouhou-navy);
            font-size: 0.95rem;
            margin-bottom: 0.35rem;
        }

        .kouhou-channel-card span {
            color: var(--kouhou-muted);
            font-size: 0.8rem;
            line-height: 1.5;
        }

        .kouhou-result-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            padding: 0.9rem 1rem;
            margin: 0.75rem 0 0.85rem;
            border: 1px solid var(--kouhou-border);
            border-radius: 18px;
            background: linear-gradient(180deg, #ffffff, #f8fbff);
        }

        .kouhou-result-title {
            color: var(--kouhou-navy);
            font-size: 0.95rem;
            font-weight: 800;
            letter-spacing: -0.01em;
        }

        .kouhou-result-caption {
            color: var(--kouhou-muted);
            font-size: 0.78rem;
            margin-top: 0.18rem;
        }

        .kouhou-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 5.4rem;
            padding: 0.42rem 0.72rem;
            border-radius: 999px;
            background: #eef6ff;
            color: var(--kouhou-blue);
            font-size: 0.82rem;
            font-weight: 800;
            white-space: nowrap;
        }

        .kouhou-revise-note {
            margin: 0.9rem 0 0.4rem;
            color: var(--kouhou-muted);
            font-size: 0.86rem;
        }

        div.stButton > button[kind="primary"] {
            border: 0;
            border-radius: 999px;
            padding: 0.72rem 1.5rem;
            background: linear-gradient(135deg, var(--kouhou-blue), var(--kouhou-navy)) !important;
            background-color: var(--kouhou-blue) !important;
            box-shadow: 0 12px 26px rgba(29, 95, 159, 0.22);
            font-weight: 800;
            color: #ffffff !important;
        }

        div.stButton > button[kind="primary"]:hover,
        div.stButton > button[kind="primary"]:focus,
        div.stButton > button[kind="primary"]:active {
            border: 0 !important;
            background: linear-gradient(135deg, #246fae, var(--kouhou-navy)) !important;
            background-color: #246fae !important;
            color: #ffffff !important;
            box-shadow: 0 14px 30px rgba(29, 95, 159, 0.28);
        }

        div.stButton > button[kind="primary"] p,
        div.stButton > button[kind="primary"] span {
            color: #ffffff !important;
        }

        div.stButton > button,
        div.stDownloadButton > button {
            border-radius: 999px;
            border-color: rgba(16, 42, 67, 0.16);
            background: #ffffff !important;
            color: var(--kouhou-navy) !important;
        }

        div.stButton > button p,
        div.stButton > button span,
        div.stDownloadButton > button p,
        div.stDownloadButton > button span {
            color: inherit;
        }

        div[data-testid="stTextArea"] textarea,
        div[data-testid="stTextInput"] input {
            border-radius: 16px;
            background: #ffffff !important;
            color: var(--kouhou-ink) !important;
            caret-color: var(--kouhou-blue);
        }

        div[data-testid="stTextArea"] textarea {
            border: 1px solid rgba(16, 42, 67, 0.18);
            box-shadow: inset 0 1px 0 rgba(16, 42, 67, 0.03);
            line-height: 1.75;
        }

        div[data-testid="stTextArea"] textarea::placeholder,
        div[data-testid="stTextInput"] input::placeholder {
            color: #94a3b8;
            opacity: 1;
        }

        div[data-testid="stRadio"] label,
        div[data-testid="stCheckbox"] label {
            color: var(--kouhou-ink);
        }

        div[data-testid="stSelectbox"] [data-baseweb="select"] > div {
            background: #ffffff !important;
            border-color: rgba(16, 42, 67, 0.2) !important;
            color: var(--kouhou-ink) !important;
        }

        div[data-testid="stSelectbox"] [data-baseweb="select"] span,
        div[data-testid="stSelectbox"] [data-baseweb="select"] svg {
            color: var(--kouhou-ink) !important;
            fill: var(--kouhou-ink) !important;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 0.4rem;
        }

        .stTabs [data-baseweb="tab"] {
            border-radius: 999px;
            padding: 0.55rem 1rem;
            background: rgba(255, 255, 255, 0.72);
            color: var(--kouhou-navy);
        }

        .stTabs [data-baseweb="tab"] p,
        .stTabs [data-baseweb="tab"] span {
            color: var(--kouhou-navy);
        }

        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid var(--kouhou-border);
            border-radius: 18px;
            padding: 0.75rem;
        }

        div[data-testid="stMetric"] label,
        div[data-testid="stMetric"] div {
            color: var(--kouhou-ink);
        }

        div[data-testid="stExpander"] {
            background: rgba(255, 255, 255, 0.82);
            border-color: var(--kouhou-border);
            border-radius: 18px;
        }

        div[data-testid="stExpander"] details,
        div[data-testid="stExpander"] summary {
            background: #ffffff !important;
            color: var(--kouhou-ink) !important;
        }

        div[data-testid="stExpander"] summary p,
        div[data-testid="stExpander"] summary span,
        div[data-testid="stExpander"] summary svg {
            color: var(--kouhou-ink) !important;
            fill: var(--kouhou-ink) !important;
        }

        div[data-testid="stAlert"] {
            background: #ffffff !important;
            color: var(--kouhou-ink) !important;
            border: 1px solid var(--kouhou-border);
        }

        div[data-testid="stAlert"] p,
        div[data-testid="stAlert"] span {
            color: var(--kouhou-ink) !important;
        }

        @media (max-width: 760px) {
            .block-container {
                padding-top: 1.2rem;
            }
            .kouhou-hero {
                padding: 1.35rem;
                border-radius: 22px;
            }
            .kouhou-steps,
            .kouhou-channel-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    st.markdown(
        """
        <section class="kouhou-hero">
            <div class="kouhou-kicker">IWAKI JC PUBLIC RELATIONS</div>
            <h1>いわきJC<br>広報アシスタント</h1>
            <p>
                議案書から、LINE・X・Facebook・Instagram・HP掲載文まで。
                目的、対象、実務情報を読み取り、媒体ごとの言葉へ整えます。
            </p>
            <div class="kouhou-steps">
                <div class="kouhou-step"><strong>1. Input</strong><span>議案書を貼り付け、またはファイルをアップロード</span></div>
                <div class="kouhou-step"><strong>2. Structure</strong><span>中間サマリで事業情報を整理</span></div>
                <div class="kouhou-step"><strong>3. Publish</strong><span>5媒体の文面を生成・修正・DL</span></div>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _section_header(step: str, title: str, caption: str) -> None:
    st.markdown(
        f"""
        <span class="kouhou-card-start"></span>
        <div class="kouhou-section-label">{step}</div>
        <h2 class="kouhou-section-title">{title}</h2>
        <p class="kouhou-section-caption">{caption}</p>
        """,
        unsafe_allow_html=True,
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

    _render_hero()
    entered = st.text_input("パスワード", type="password")
    if st.button("ログイン"):
        if entered == password:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")
    return False


def _get_client() -> ClaudeClient | None:
    api_key = _get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        st.error(
            "ANTHROPIC_API_KEY が設定されていません。`.env` か `.streamlit/secrets.toml` に追加してください。"
        )
        return None
    return create_client(api_key)


def _input_section() -> str | None:
    """議案書テキストを取得。"""
    _section_header(
        "STEP 01",
        "議案書を入力",
        "本文貼り付け、または PDF / Word / テキストファイルから読み取ります。",
    )
    input_method = st.radio(
        "入力方法",
        ["テキストを貼り付け", "ファイルをアップロード"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if input_method == "テキストを貼り付け":
        pasted = st.text_area(
            "議案書の本文を貼り付けてください",
            height=300,
            key="pasted_text",
            placeholder="第○回通常総会 第○号議案 ○○事業（案）...",
        )
        if pasted.strip():
            return pasted.strip()
        return None

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
    _section_header(
        "STEP 02",
        "生成するチャネルを選ぶ",
        "対内向けと対外向けを必要に応じて選択します。",
    )
    st.markdown(
        """
        <div class="kouhou-channel-grid">
            <div class="kouhou-channel-card"><strong>LINE</strong><span>会員向けの案内文。日時、服装、出欠など実務情報を整理。</span></div>
            <div class="kouhou-channel-card"><strong>X</strong><span>告知から事後報告まで、時系列の5本セット。</span></div>
            <div class="kouhou-channel-card"><strong>Facebook</strong><span>事業の背景と意義を伝える対外向け長文。</span></div>
            <div class="kouhou-channel-card"><strong>Instagram</strong><span>スマホで読みやすいキャプションとハッシュタグ。</span></div>
            <div class="kouhou-channel-card"><strong>HP</strong><span>公式掲載に使いやすい見出し付き本文。</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
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
    client: ClaudeClient,
    document_text: str,
    model_label: str,
) -> dict[str, Any] | None:
    """サマリをセッションに作る or 取り出す。"""
    cache_key = "summary"
    text_hash = _stable_digest(document_text, model_label)

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
        except ClaudeApiError as exc:
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
    client: ClaudeClient,
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
        except ClaudeApiError as exc:
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
    st.caption(f"目安: {target}")


def _render_revision_form(
    client: ClaudeClient,
    channel_id: str,
    current_text: str,
    configs: Configs,
    model_label: str,
) -> None:
    """「ここをこう直して」フォーム。"""
    label = configs.channel(channel_id)["label"]
    st.markdown(
        '<p class="kouhou-revise-note">AIで整え直したい場合は、修正指示を短く入力してください。</p>',
        unsafe_allow_html=True,
    )
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
            except ClaudeApiError as exc:
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
    client: ClaudeClient,
    summary: dict[str, Any],
    selected_channels: list[str],
    configs: Configs,
    model_label: str,
    extra_instructions: str,
) -> None:
    _section_header(
        "STEP 03",
        "生成された広報文",
        "直接編集、AI書き直し、ダウンロードまでここで完結します。",
    )
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
            st.markdown(
                f"""
                <div class="kouhou-result-toolbar">
                    <div>
                        <div class="kouhou-result-title">{channel_cfg["label"]}</div>
                        <div class="kouhou-result-caption">本文は直接編集できます。必要に応じてAI書き直しやDLを使ってください。</div>
                    </div>
                    <div class="kouhou-pill">{len(text)}字</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
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


def _stable_digest(*values: str) -> str:
    """入力内容の同一性判定をプロセスに依存しない形にする。"""
    digest = sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _clear_generated_state() -> None:
    """議案書や生成条件が変わった時に古い結果を混ぜない。"""
    for key in list(st.session_state.keys()):
        if key.startswith(("result_", "editor_", "history_")) or key in (
            "summary",
            "summary_hash",
            "active_generation_key",
        ):
            st.session_state.pop(key, None)


def main() -> None:
    _inject_theme_css()
    if not _check_password():
        return

    _render_hero()

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
    )

    generation_key = ""
    if document_text:
        generation_key = _stable_digest(document_text, model_label, extra_instructions)

    if do_generate and document_text:
        _clear_generated_state()
        st.session_state["active_generation_key"] = generation_key

    can_render_results = (
        bool(document_text)
        and st.session_state.get("active_generation_key") == generation_key
    )

    if document_text and not do_generate and "summary" in st.session_state and not can_render_results:
        st.info("入力内容または生成条件が変更されています。新しい内容で作る場合は「広報文を生成 / 再構築」を押してください。")

    if can_render_results:
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
