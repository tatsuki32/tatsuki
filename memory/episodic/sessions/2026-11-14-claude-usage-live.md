# 2026-11-14 Claude Usage をリアルタイムに

## ひとこと
- いま：Claude の使用量を5分ごとに見られる
- 次：本番サーバーの再起動で反映
- 待ち：コミットするかの判断

## やったこと
- `desk/claude_usage.py` を作り直した。AI を使わず `~/.claude/projects/**/*.jsonl` を数える。ファイルごとに読んだ位置（バイト）を `desk/data/claude-usage/cache.json` に覚え、増えた分だけ読む。初回 約2秒、2回目以降 約0.1秒（会話の記録は全部で約1.1GB）。
- 今日・昨日・直近5時間・時間帯・会話ごと・定期作業の一覧（Claude の定期タスク／launchd／サーバー内）を `live.json` に置く。`server.py` が 4747 で5分ごと（`claude_usage.watch`）、`POST /api/claude-usage/refresh` で今すぐ。
- 画面 `cuView` を作り直した。前からある日別の表（`~/ClaudeUsage/ledger.json`）は「History」の折りたたみに残した。
- `backup_data.py` の `IGNORE` に `claude-usage/`。

## 分かったこと（技術）
- Claude の定期タスクの本当の cron・オンオフ・最後の実行は `~/Library/Application Support/Claude/claude-code-sessions/<id>/<id>/scheduled-tasks.json` の `scheduledTasks`。cron は Mac の時刻。
- 上限のお知らせは会話の記録に `"isApiErrorMessage":true` の行として残る（例：週の上限・5時間の上限・モデル別の上限）。これを数えると、上限で止まった定期実行が分かる。
- 会話の題は `custom-title`（`customTitle`）の行。何度も追記されるので最後のものを使う。
- Desk のページには viewport の meta がないので、ブラウザの mobile 表示の試験では幅が 980px になる。375px は iframe で確かめた。

## 残り
- 本番（4747）はサーバーの再起動まで5分ごとの集計が動かない（画面を開いたときは古ければその場で数える）。
- デザインの本は全文（約18万字）が会話の容量に入らず読めなかった。`design-nondesigner.md` のルールとチェックリストで確かめた。
