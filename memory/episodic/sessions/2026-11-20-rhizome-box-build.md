# 2026-11-20 Rhizome Box 本番の実装（途中）

## ひとこと
- いま：サーバーと試験ができた
- 次：画面を移して試しの住所に置く
- 待ち：なし

## やったこと
- 要件定義（承認済み）に沿って `rhizomebox/` を作り始めた。
  - `public/app/notation.js`：記法の読み取り（サーバーとブラウザで共通）
  - `src/app.js`：ログイン（メールの6桁コード → 90日のセッション）、ページの API（保存・履歴・上書き防止・題の変更でリンクも直す）、非公開の守り（訪問者向けは全部 `visibleSql`）、画像（R2、SVG は断る）、リンク先の題（`safeTarget` で内向きの住所に行かない）、書き出し（BACKUP_KEY）、サイトマップ、サーバーで作る HTML（`src/html.js`）
  - `src/index.js`：Cloudflare の送信でログインのメール（Sift と同じ）
  - `test/`：15件すべて通る（`cd rhizomebox && npm test`）。非公開がどこからも訪問者に出ないことを確かめる
  - `public/app/app.css`：試作の見た目を移した
- Cloudflare に D1 `rhizomebox` と R2 `rhizomebox-files` を作った。まだデプロイしていない。

## 決めたこと
- ログインは Cloudflare Access ではなく、Sift と同じ Cloudflare のメール送信で6桁のコードを送る（ダッシュボードの作業が要らない）。
- 試しは workers.dev の住所（STAGE=1 で検索エンジンに出さない）。rhizomebox.tany.im につなぐのは本人の OK のあと。

## 中断した場所
- 画面（`public/app/app.js`）の移植。試作のスクリプトは scratchpad の `parts/mock.js`（Artifact 版13 の中身）。メモ通りに、データをサーバーの index と1ページずつの取得に替え、保存・競合・つながらないとき・ログイン画面・履歴・削除・道（URL）の扱いを足す。`app.js` の `shellHtml` の view に `new` を足す。

## 次の一手
- `app.js` を移植 → `npx wrangler deploy`（秘密 OWNER_EMAIL・BACKUP_KEY を入れる、schema を remote に流す、avatar.jpg を R2 に置き tatsuki と settings のページを作る）→ 試しの住所で通し操作 → Mac への保管と Desk 登録 → 本人に公開の OK をもらう。
