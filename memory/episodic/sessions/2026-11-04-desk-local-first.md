# 2026-11-04 Desk を Mac だけで使う形に組み替え

## ひとこと
- いま：Desk が Mac の専用アプリで動く
- 次：朝の定期タスクを試す
- 待ち：定期タスクの試運転

## やったこと
- 専用アプリ `desk/app/`（Swift の WKWebView。外部リンクは既定のブラウザ。サーバーが止まっていれば launchctl で起動）。
- データを `desk/data/` に統合。一覧物は `docs/<collection>/<id>.json` を正本にし、ページ用の `*.js` を `server.py` の `rebuild_feed` で作る。
- `desk/sync.py`：`put` / `get` / `mail` / `build` を追加。オンライン公開用のコマンドは削除。`merge` は取り込みだけに使う。
- `desk/backup_data.py`：`desk/data/` を独立した Git リポジトリとして15分ごとに保管。キーらしい文字列で止まる。
- `/mail-send`：Desk のタスクから Terminal で起動。会話で承認を得てから Gmail で送る。
- 定期タスクは `desk/sync/SCHEDULED_TASKS.md`。手順本文は `~/.claude/scheduled-tasks/`（個人情報があるのでリポジトリに入れない）。

## 決定
- 要件定義：https://claude.ai/artifact/AY3dYUjuxPTpnBjjcqDdFq
- データは公開リポジトリと分ける。非公開の別リポジトリ `tatsuki-desk-data`。
- オンライン版のページとデータは消さない。受け入れ確認まで残す。

## 保管
- 非公開リポジトリに接続済み。認証は `desk/data` の Git 設定で `gh auth git-credential` を使う（全体の Git 設定は変えていない）。

## 残り
- 定期タスクの試運転、クラウド定期タスクの停止、オンライン版の最後の差分の取り込み。
- Terminal の claude で Gmail の送信ツールが使えるかの確認。
- デザインの本は Drive の文字変換が p.136 までで、後半はまとめで補った。
