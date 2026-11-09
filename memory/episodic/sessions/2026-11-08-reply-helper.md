# 2026-11-08 返信のたたき台（reply-helper）

## ひとこと
- いま：英語の返信案を3つ出すページを作った
- 次：Mac で Cloudflare に公開して友達に送る
- 待ち：公開の手順（キーとパスワード入れ）

## やったこと
- `reply-helper/`：Cloudflare Workers 1本。`src/index.js`（API）と `src/page.js`（画面）。
  - `POST /api/login`・`POST /api/reply`。パスワードは secret `APP_PASSWORD`、キーは `ANTHROPIC_API_KEY`。毎回サーバーで照合。
  - Claude は `claude-opus-5-5`・effort low・構造化出力（json_schema）。`fallbacks:"default"`（beta `server-side-fallback-2026-07-01`）。
  - 返り値：`summary_ja` と `replies[{tone: soft|firm|short, en, ja}]`。貼った文章は保存しない。
- 画面は黒＋金、左ぞろえ1本、文字 12/14/16/20、375px で横スクロールなし（スクリーンショットで確認）。
- `ANTHROPIC_BASE_URL` で偽 API に向けて手元テストできる（本番では入れない）。

## 決めたこと
- 権限の違いは作らない。入口はパスワード1つ。
- パスワードはリポジトリに書かない（公開リポジトリのため）。

## 本の読み方
- デザインの本は Drive の変換の終わり（p.136）まで全部読んだ。後半（文字組み）は Drive に無く未読。
