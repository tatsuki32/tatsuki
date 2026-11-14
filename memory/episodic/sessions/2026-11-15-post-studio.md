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

## リンクの扱い（同じ日）
- 投稿先ごとの `link`：`inline`（本文に1回）、`bio`（Instagram の投稿。本文に入れず「プロフィールに」）、`sticker`（ストーリー。リンクスタンプは手で足す）、`none`。
- `fix_links` が、渡していない URL を消し、本文に入れない先からは URL を消し、入れる先で抜けていれば最後に足す。古い `targets.json` には例のファイルから既定を補う。
- 短縮リンクと計測（Bitly や Meta Pixel のようなもの）は作っていない。作るなら Cloudflare Workers の転送＋クリックの記録で、Pixel は自分のサイトにだけ置ける。

## 短縮リンク go.tany.im（同じ日。要件定義 https://claude.ai/artifact/TG14nCDMK33m9qRCryDqxR の1番）
- Worker `golinks/`（`tatsuki-go`、custom domain go.tany.im、D1 `tatsuki-go`）。`/api/` は `DESK_KEY` の Bearer だけ。同じリンク・同じ先・同じ投稿なら同じ短縮リンクを返す。
- Desk は `desk/golinks.py`。Post Studio は案を作る前に投稿先ごとの短縮リンクを作って `post.short` に残し、Claude にはその先の分だけを渡す。
- ロボットの判定は User-Agent の正規表現と HEAD。試験のリンクは `DELETE /api/links/<code>` で止めた。
- 次：要件定義の2番（予約の土台：Queue、ルーティン、R2、5分ごとの送信）。

## 自動投稿（同じ日）
- Meta アプリ「Tatsuki Post Studio」（ポートフォリオ「Tatsuki Mitsuhashi 三橋龍起」）。新しいアプリは Instagram ログイン不可 → 統合ログイン（ログイン設定 1123461540280904、システムユーザーの鍵＝期限なし）。許可は use case の「アクセス許可と機能」で先に「追加」しないと設定に出ない。
- Instagram に出ないアカウントは、Meta ビジネスの 設定→Instagramアカウント→追加 でポートフォリオに入れると出る。
- Threads の戻り先の欄はコンボボックス。打ったあと出る候補を選ぶと確定（Enter では入らない）。テスターの招待はアプリの 設定→More settings→Website permissions→Invitations。ウェブのログインは電話認証で止まることがある（tatsuki3284 の Threads は未接続）。
- R2 は未申し込み（code 10042）なので画像は KV `tatsuki-go-media`。動画は R2 の申し込みが要る。
- 試しの1件を tatsu_languages の Threads に投稿できた（本人の OK のあと）。
- 次：動画（リール・YouTube・TikTok）、Facebook ページ、LinkedIn、DM を Replies へ。
