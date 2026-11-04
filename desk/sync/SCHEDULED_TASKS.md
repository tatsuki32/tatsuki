# Tatsuki's Desk の定期タスク（Mac の Claude スケジュールタスク）

データの正本は Mac の `~/TatsukiDesk/desk/data/` だけです。オンライン版とは同期しません。
タスクは Claude アプリが開いている間に動きます。閉じていた間の分は、次に開いたときに動きます。

## 書き込み方（全タスク共通）

作業フォルダは `~/TatsukiDesk`。JSON を一時ファイルに書き、次を実行します。

```bash
python3 desk/sync.py put <collection> <doc_id> <file.json>   # 保存して、ページのデータも作り直す
python3 desk/sync.py get <collection> <doc_id>               # 1件読む（なければ {}）
python3 desk/write_feed.py calendar <raw.json>               # 予定：必要な項目だけ残して保存
python3 desk/write_feed.py gtasks <raw.json>                 # Google タスク：同上
```

- 使えるコレクション：`editions`、`days`、`watch`、`reports`、`calendar`、`gtasks`、`tools`、`memos`、`tasks`、`notes`、`habits`、`wins`。
- `memos` や `tasks` はサーバー経由で書きます。`desk/data/*.json` を手で書き換えません。
- メール本文やメモの中身を、報告やログに書きません。一時ファイルは使い終わったら消します。

## タスク一覧

| ID | 時刻（ホーチミン） | 内容 | 書く先 |
|---|---|---|---|
| `desk-calendar-gtasks` | 毎時 | メモ連携（`python3 lifeos/11-notes/cli.py run`）、予定7日分、Google タスク | `calendar`、`gtasks` |
| `desk-world-brief` | 毎日 7:17 | 世界ニュース | `editions` |
| `desk-morning-mail` | 毎日 7:22 | Gmail の確認と返信の下書き（送信しない） | `days` |
| `desk-minor-watch` | 火・金 7:33 | マイナー国ウォッチ | `watch` |
| `desk-claude-daily-report` | 毎日 7:40 | 前日の Claude の作業レポート | `reports` |
| `desk-sunday-mirror` | 日曜 17:08 | 日曜の鏡 | `memos` |

各タスクの手順は Mac の `~/.claude/scheduled-tasks/<ID>/SKILL.md` にあります。経歴やドキュメントの ID を含むので、このリポジトリには入れません。
