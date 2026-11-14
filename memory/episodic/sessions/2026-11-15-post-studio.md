# 2026-11-15 Post Studio（SNS 投稿の書き分け）

## ひとこと
- いま：写真とメモから投稿案を作れる
- 次：本物の写真で使ってみる
- 待ち：Facebook ページと Meta の開発者アプリ

## やったこと
- 要件定義：https://claude.ai/artifact/ERP5mD1eJeC4pMcgcWhnN2 （第1段階＝案を作るまで。投稿はしない）
- `desk/posts.py`：写真の受け取り（sips で JPEG、同じ写真は1回）、Claude API に1回でまとめて頼む、文字数はコードが数える（X は twitter-text v3 の重み）、長い案だけ2回まで書き直し、他人の名前は案から外して一覧に出す。
- `desk/app/postcrop.swift`：`focus`（Vision の顔→目立つ所）、`crop`（大きさに切ってストーリーに文字）、`clip-html`（note 用の書式つきコピー）。初回に自動ビルド（`~/Library/Application Support/TatsukiDesk/postcrop`）。
- 画面 `#post`：New・Earlier・Settings。カードは言語ごと。Copy and open（X・Threads・LinkedIn は文入りの投稿画面）、Save picture（utilities のトークンでブラウザが保存）、Rewrite（直したい点を一言）、Posted。
- 投稿先の設定 `desk/data/posts/targets.json`（例は `desk/posts_targets.example.json`）。口調は Drive の過去のキャプションと note の記事から作った。

## つまずき
- サンドボックスの中では Vision が顔を見つけない（中央の既定になる）。サーバーから動かすと見つかる。
- Instagram のハッシュタグ上限は「5個」と広く報じられているが、公式の Help Center では確かめられなかった。設定は5。
- アプリの窓のダウンロードは `/api/utilities/file/` だけがブラウザに渡る（main.swift）。新しい道を作らず、そのトークンを使った。
