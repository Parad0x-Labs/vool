---
description: VOOLの作り。一ページでわかるアーキテクチャ、開発者向けのセットアップ、そして貢献の方法。
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# アーキテクチャとコントリビュート

## 一ページでわかるアーキテクチャ

VOOLは、配信されるコアを持つ、ローカルファーストのデスクトップランタイムです。

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

コードが守らせている原則は次のとおりです。

* **標準はローカル、クラウドはご自身のキーで。** クラウドのレーンは、ご自身の認証情報と上限があるときだけ存在します。
* **一つの判断に一つの権限主体。** 権限、支出、ネットワークへの送信には、それぞれ担当する契約が一つあります。障害は、それを担当する境界で分類されます。
* **何が失敗したかを伝える。** 不明な結果は不明として型付けされ、拒否は何が起きなかったかを伝え、ピン留めしたモデルが黙って置き換えられることは決してありません。
* **すべてを記録。** ツール呼び出し、承認、支払い、アップデートは、プロファイルにレシートを書き込みます。

## 開発者向けのセットアップ

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

プラットフォームに関する注意事項、ツールチェーンの全体構成、リリースのゲートは、リポジトリのエンジニアリング向けのドキュメントにあります（リポジトリのルートにある `docs/`。内部の詳細は、この公開ドキュメントサイトには含まれていません）。

## ドキュメント：唯一の正式なソース

このドキュメントは **一つの** ソースから生成されています。このリポジトリの `docs/` フォルダです（GitBookの構成、リポジトリのルートに `.gitbook.yaml`）。公開ドキュメントサイトは、ここからビルドされます。ドキュメントの作業をするには：

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

エラーリファレンス（`ERROR_BOOK.md`）とローカライズの引き渡しマニフェストは、**ランタイム自身のレジストリから生成** されます。手で編集せず、再生成してください。

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

生成されたファイルがレジストリと食い違うと、専用のテストがビルドを失敗させます。

## 貢献する

* 小さく、レビューされた変更にしてください。ドキュメントのすべての記述は、出荷されたコードと一致している必要があります。
* 安定したコードと識別子は維持してください。それらはビルドよりも長く使われます。
* 権限を決して黙って広げないでください。新しい権限、支出の経路、送信のレーンは設計の変更であり、パッチではありません。
* ベータ版の制限は、隠さずに文書化します。文書化されていない制限を見つけたら、それはドキュメントのバグです。
