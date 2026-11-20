# 2026-11-20 Rhizome Box 本番の実装（途中）

## ひとこと
- いま：試しの住所で動き始めた
- 次：本人が試して公開を決める
- 待ち：一度ログインして書いてみる

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

## 追記：画面を移して試しの住所に置いた
- 送り主のアドレスはコードに書かず Worker の秘密 `MAIL_FROM` にした（コミット前フックに止められたため）。
- `public/app/app.js`：試作のスクリプトを移し、データをサーバーの index と1ページずつの取得に替えた。0.7秒後にまとめて保存、つながらないときは端末に残して送り直す、2台で同時に書いたら「どちらを残すか」、ログイン画面、履歴（前の版に戻す）、削除、URL はページの題（空白は _）。新しく作って何も書かずに離れたページは消える。
- 手元（`wrangler dev`）でブラウザを操作して確かめた：フォームでログイン（コードは手元のメールの記録から）、打った文字が保存される、空のリンクから新しいページ、公開の切り替え、訪問者には非公開のリンクがただの文字・非公開ページは 404・編集できない、375px で横にはみ出さない、暗い見た目、グラフ。
- 共有カードの画像 `public/og.png`（1200×630）とアイコン `favicon.svg` を作った。
- 試しの住所（workers.dev、`STAGE=1` で検索エンジンに出さない）に置き、秘密3つ（OWNER_EMAIL・MAIL_FROM・BACKUP_KEY）を入れた。アバターを R2 に置き、tatsuki（公開）と settings（非公開）のページを作った。
- Mac への毎日の写し `desk/rhizomebox_backup.py` を server.py から動かす。1回動かして2ページと画像1枚を写せた。

## 次の一手
- 本人が試しの住所でメールのコードでログインし、数行書く（iPhone でも）。
- OK が出たら：wrangler.toml の STAGE を外し BASE_URL と rhizomebox.tany.im の custom domain を足して deploy、`desk/data/rhizomebox/config.json` を消す、Search Console に登録、ツールボックスに登録。
