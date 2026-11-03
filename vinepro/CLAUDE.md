# CLAUDE.md — VINEPRO ボード（自前サーバー版）

VINEPRO ACADEMY（ホーチミンの動画クリエイター養成スクール）の業務フロー・100日スケジュールを、スタッフが同時編集するホワイトボード。UI とデータは日本語・ベトナム語・英語。詳しくは `README.md`。

## 構成
- `index.html` — ボード本体。claude.ai の Artifact 版と同じファイル（`<html>` を持たない。`server.py` の `board_page()` が包む）。**ここはなるべく変えない**。変えたら claude.ai 版にも同じ変更を入れる。
- `static/shim.js` — `window.claude`（db / user / room / sample）をこのサーバーにつなぐアダプター＋右上のアカウントメニュー。
- `server.py` — Python 標準ライブラリだけ。SQLite（`var/board.db`、`VB_DATA_DIR` で変更）。認証・権限・履歴・SSE。
- `static/login.html` `static/admin.html` — 3言語。文言を足すときは ja / vi / en すべてに足す。
- `dev/` — claude.ai なしで index.html だけを見るためのモック（`python3 dev/build_local.py`）。

## ルール
- `data/`・`var/` は社内データ。**このリポジトリは公開なので絶対にコミットしない**（`.gitignore` 済み）。
- 権限は owner（1人）/ editor / viewer。owner の付与は CLI（`server.py adduser --role owner`）だけ。
- 書き込みは必ず `write_doc()` を通す（履歴と配信がここにある）。
- iPhone 幅 375px で横スクロールが出ないこと。

## 確認
```bash
VB_DATA_DIR=/tmp/vb python3 server.py adduser --email o@example.com --name O --role owner --password password1
VB_DATA_DIR=/tmp/vb python3 server.py   # → http://localhost:4848
```
