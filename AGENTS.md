# AGENTS.md — 開発ルールとガイドライン

このファイルは Codex / Claude Code などの AI エージェントがこのリポジトリを操作する際に従うべきルールをまとめたものです。

## 言語

- **コード内コメント・ドキュメント文字列（docstring）**: 日本語
- **変数名・関数名・クラス名**: 英語スネークケース（例: `extract_summary`, `channel_id`）
- **YAML キー**: 英語スネークケース
- **Markdown ドキュメント**: 日本語
- **AIへの回答**: 日本語

---

## プロジェクト構造の原則

```
app.py          UI ロジックのみ。API 呼び出しは src/ に委譲する。
src/extract.py  ファイル読み取り専用。Ollama API に依存しない。
src/generate.py Ollama Cloud API 呼び出し専用。Streamlit に依存しない。
prompts/        プロンプトは .md ファイルで管理。コード内にハードコードしない。
config/         団体固有情報・スタイルガイドは YAML で管理。
```

- `app.py` から `ollama` を直接インポートしない（型ヒントは除く）
- `src/generate.py` から `streamlit` を直接インポートしない
- 新しいチャネルを追加する場合は `prompts/<channel>.md` と `config/channels.yaml` に追記し、`generate.py` の `_max_tokens_for_channel()` にも追加する

---

## コーディング規約

### Python

- `from __future__ import annotations` を各ファイル冒頭に記載
- 型ヒントを必ず付ける（`Any` は必要最小限に留める）
- 関数の戻り値型も必ず明示する
- 例外は具体的な型で捕捉する（`except Exception` は UI 層のみ許容）
- f-string を優先する（`.format()` は使わない）
- コメントは「なぜそうしているか（WHY）」のみ書く。コードが読めば分かる内容は書かない

### Streamlit セッション管理

- チャネルごとのセッションキーは以下の命名規則を守る:
  - `result_{channel_id}` — 生成済みテキスト
  - `editor_{channel_id}` — テキストエリアの編集中状態
  - `history_{channel_id}` — 修正履歴リスト
- `_set_result()` を介してのみ `result_` と `editor_` を更新する（直接 `st.session_state` に代入しない）
- 新しいセッションキーを追加する際は `do_generate` 時のリセット処理（`app.py` の `startswith(...)` タプル）にも追加する

### YAML / プロンプト

- `config/lom.yaml` はいわきJC固有の情報のみ。他のLOMの情報を入れない
- `prompts/line.md` の参考実例（まちづくり委員会・俣田 の例）はフォーマット参考専用。実例の人名・事業内容を生成文に流用しないこと（プロンプト内に警告注記あり）
- `prompts/extract.md` のスキーマを変更した場合は `src/generate.py` や `app.py` の参照箇所も確認する

---

## Ollama Cloud API の使い方

### モデル定義

`src/generate.py` の `MODELS` dict で管理する。新モデルを追加する場合はここに追記する。

```python
MODELS: dict[str, dict[str, Any]] = {
    "GPT-OSS 20B（無料枠・高速）": {
        "id": "gpt-oss:20b",
        "think": "low",
        "temperature": 0.7,
    },
    "Gemma 4 31B Cloud（自然文・構造化）": {
        "id": "gemma4:31b-cloud",
        "think": "medium",
        "temperature": 0.7,
    },
    ...
}
DEFAULT_MODEL_LABEL = "GPT-OSS 20B（無料枠・高速）"
```

- Ollama Cloud 直結時は `https://ollama.com` を host にし、`Authorization: Bearer <OLLAMA_API_KEY>` を付ける
- ローカル Ollama 経由の `*-cloud` モデル名ではなく、直結 API では `gpt-oss:20b` / `gpt-oss:120b` を使う
- GPT-OSS の思考量は `think: "low" | "medium" | "high"` で制御する
- 出力長は `options.num_predict` で制御する

### ストリーミング

`_stream_text()` で `client.chat(..., stream=True)` を使う。タイムアウト回避のため全レスポンスでストリーミングを維持すること。

---

## 変更時に必ず確認すること

| 変更箇所 | 確認事項 |
|----------|----------|
| `MODELS` dict の追加・変更 | `_build_request_kwargs()` で `model` / `options` が正しく渡されるか |
| `prompts/extract.md` のスキーマ変更 | `app.py` の `summary.get(...)` 参照、`generate.py` の `_build_user_payload()` |
| `config/channels.yaml` へのチャネル追加 | `app.py` の `_channel_selection()`、`generate.py` の `_max_tokens_for_channel()` |
| セッションキーの追加 | `do_generate` 時のリセット処理（`app.py` 423行付近） |
| `prompts/line.md` の実例変更 | 実例の人名・事業内容を AI が流用しないよう警告注記が残っているか確認 |

---

## テスト・ビルド方法

現状、自動テストは未整備です。変更後は以下の手順で手動確認してください。

### 起動確認

```bash
.venv\Scripts\activate   # Windows
streamlit run app.py
```

### 動作確認チェックリスト

1. テキスト貼り付けタブで議案書サンプルを入力 → 「広報文を生成」ボタンをクリック
2. 中間サマリ（JSON）が正しく生成されるか確認
3. LINE / X / Facebook / Instagram / HP の各タブで広報文が生成されるか確認
4. 「ここをこう直して」フォームで修正指示を入力 → 書き直しが動作するか確認
5. 修正履歴が表示され、「この版に戻す」が動作するか確認
6. PDF または Word ファイルをアップロードしてテキスト抽出が動作するか確認

### よくあるエラー

- **`AttributeError: 'NoneType' object has no attribute ...`**: セッションキーの命名ズレが多い。`result_` / `editor_` / `history_` のプレフィックスを確認
- **`json.JSONDecodeError`**: `_parse_json_response()` がコードブロックを除去しているが、モデルの稀な出力崩れで発生することがある。`generate.py` の正規表現 fallback を確認
- **`ollama.ResponseError`**: `OLLAMA_API_KEY`、モデルID、Ollama Cloud の利用枠を確認

---

## 依存パッケージの更新

```bash
pip install -r requirements.txt --upgrade
# バージョン確認
pip list | grep -E "streamlit|ollama|pypdf|docx|yaml"
```

`ollama` パッケージの破壊的変更に注意。アップデート後は API 呼び出し部分（`src/generate.py`）を重点的に確認する。
