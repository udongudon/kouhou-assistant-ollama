# いわきJC 広報アシスタント

公益社団法人いわき青年会議所（いわきJC）の広報委員会向けに、議案書（事業計画書）を入力するだけで5チャネルの広報文を自動生成する Streamlit アプリです。

## 機能概要

- **入力**: 議案書テキストの貼り付け、または PDF / Word(.docx) / テキストファイルのアップロード
- **中間サマリ**: 議案書から JSON 形式の構造化データを自動抽出（2段階パイプライン）
- **5チャネル生成**:
  - LINE（会員グループ向け・対内告知）
  - X / 旧Twitter（5本セット：告知 → 1週間前 → 前日 → 当日 → 事後報告）
  - Facebook（エピソード調・対外）
  - Instagram（キャプション・対外）
  - HP掲載文（見出し構造・対外）
- **修正機能**: 「ここをこう直して」AI 書き直し、手動直接編集、修正履歴と版の復元
- **DL**: 各チャネルを .txt でダウンロード
- **パスワード保護**: `APP_PASSWORD` 環境変数で認証を有効化

## 技術スタック

| 分類 | 技術 |
|------|------|
| UI | Streamlit >= 1.40.0 |
| LLM | Ollama Cloud API (ollama >= 0.6.0) |
| PDF抽出 | pypdf >= 5.0.0 |
| Word抽出 | python-docx >= 1.1.0 |
| 設定 | PyYAML >= 6.0 |
| 環境変数 | python-dotenv >= 1.0.0 |
| 実行環境 | Python 3.11 以上推奨 |

### 使用モデル（UI から選択可）

| ラベル | モデルID | デフォルト |
|--------|----------|-----------|
| GPT-OSS 20B（無料枠・高速） | gpt-oss:20b | ✅ |
| GPT-OSS 120B（無料枠・高品質） | gpt-oss:120b | |
| Gemma 4 31B Cloud（自然文・構造化） | gemma4:31b-cloud | |

※ Qwen 3.5 は Ollama のタグ一覧に `qwen3.5:122b-a10b` などがありますが、Ollama Cloud 直結 API では `qwen3.5:cloud` のみ認識されます。`qwen3.5:cloud` は API 呼び出し時にサブスクリプションが必要なため、無料運用向けの選択肢から外しています。

## セットアップ

### 1. 前提条件

- Python 3.11 以上
- Ollama API キー（[https://ollama.com/settings/keys](https://ollama.com/settings/keys) で取得）

### 2. 仮想環境と依存パッケージ

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. 環境変数の設定

```bash
cp .env.example .env
# .env を開いて OLLAMA_API_KEY を設定
```

または `.streamlit/secrets.toml` に記述（`.streamlit/secrets.toml.example` を参照）:

```toml
OLLAMA_API_KEY = "ollama_..."
APP_PASSWORD = "任意のパスワード"   # 省略するとパスワードなしで起動
```

### 4. 起動

```bash
streamlit run app.py
```

ブラウザで `http://localhost:8501` が自動で開きます。

## 主要ディレクトリ構成

```
kouhou/
├── app.py                  # Streamlit メインアプリ（UI・セッション管理）
├── requirements.txt        # Python 依存パッケージ
├── .env.example            # 環境変数サンプル
├── .streamlit/
│   └── secrets.toml.example
├── src/
│   ├── extract.py          # PDF / Word / テキスト抽出ユーティリティ
│   └── generate.py         # Ollama Cloud API 呼び出し・プロンプト組み立て
├── prompts/
│   ├── extract.md          # 議案書 → 中間サマリ JSON 抽出プロンプト
│   ├── system_base.md      # 全チャネル共通システムプロンプト
│   ├── line.md             # LINE 向け生成プロンプト（フォーマット実例付き）
│   ├── x.md                # X 向け生成プロンプト（5本セット）
│   ├── facebook.md         # Facebook 向け生成プロンプト
│   ├── instagram.md        # Instagram 向け生成プロンプト
│   └── website.md          # HP掲載文 向け生成プロンプト
└── config/
    ├── lom.yaml            # LOM固有情報（団体名・ハッシュタグ・締め文等）
    ├── jc_style.yaml       # JC広報スタイルガイド（トンマナ・禁止表現等）
    └── channels.yaml       # チャネル別規約（文字数・必須要素・トーン）
```

## 広報文の品質を上げたい場合

- **トンマナ調整**: `config/jc_style.yaml` の `tone_principles` / `avoid_phrases` を編集
- **チャネル別の指示調整**: `prompts/<channel>.md` を直接編集
- **LOM情報更新**: `config/lom.yaml` のハッシュタグ・締め文を変更
- **モデル切替**: UI の「オプション」から GPT-OSS 120B / Gemma 4 に変更して品質を比較できる

## トラブルシュート

| 症状 | 原因 | 対処 |
|------|------|------|
| 「ファイルからテキストを抽出できませんでした」 | スキャンPDF | OCR後に再アップロード、またはテキスト貼り付けで対応 |
| 「サマリのJSON解析に失敗」 | モデルの稀な出力崩れ | GPT-OSS 120B に切り替え、または入力を短くする |
| 議案書が長い | 自動で先頭3万字に切り詰め | 事業概要部分だけをテキスト貼り付けで処理 |
