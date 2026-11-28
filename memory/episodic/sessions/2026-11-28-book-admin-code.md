# 2026-11-28 予約の管理画面のログインを6桁コードに

## ひとこと
- いま：コードのログインを本番に出した
- 次：本物のメールで入ってみる
- 待ち：なし

## やったこと
- `desk/data/tany/site/bookadmin.js`（Git の外。写しは `.backup/bookadmin.2026-11-28.js`）：ログインをメールのリンクから6桁のコードに。
  - D1 に `book_admin_codes`（`id`・`hash`＝`BOOK_KEY` つきの SHA-256・`created`・`expires`・`used` 0/1/2・`tries`）。新しいコードを出すと前のコードは `used=2`。
  - `POST …/session` は `{code}`。いちばん新しい未使用のコードだけと比べる。間違い5回でそのコードは終わり（`ENDED`）、24時間の間違いが20回で `LOCKED`（429）。
  - 件名にコード（iPhone の候補に出るように）。入力欄は `autocomplete="one-time-code"`、6桁そろうと自動で送る。
  - 前の `book_admin_tokens` は cron が空にするだけ（表は残す）。古いリンクの `#in=` はアドレス欄から消すだけ。
  - 試験ではコードがログに `FAKE ADMIN CODE` で出る。
- `CLAUDE.md` の説明を直した。

## 確かめたこと
- `tany-book-worker`（8790）で：間違ったコードの拒否、正しいコードでログイン、使い回しの拒否。
- 5回で終わる動きは、1分あたりの回数の制限（`tooMany`）に先に当たって未確認。

## 次の一手
- 2026-11-28 に本人の OK で `npx wrangler deploy` 済み（本番のページが新しい画面なのを確認）。本物のメールでコードが届くかは本人のログインで確かめる。
