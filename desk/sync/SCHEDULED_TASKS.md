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
| `desk-daily-note` | 毎日 16:00 | 3min Daily Note のメールを本人へ送り、当日のタスクを作る（`lifeos/19-daily-note/`） | `tasks` |
| `desk-task-calendar` | 毎時 | 未着手で時刻つき期日のタスクを Google カレンダーへ（`desk/task_calendar.py`） | `calendar-sync.json` |
| `desk-task-clarify` | 毎晩 2:30 | 未整理のタスクを30件まで、具体的なタイトル・まず5分・手順・出典・頼む文面に整える（`desk/task_clarify.py`。送信しない） | `tasks` の `clear` |
| `desk-task-estimate` | 毎日 6:40 | タスクにかかる時間の目安と時期の案（`desk/task_estimate.py`。本人の選択は変えない） | `tasks` の `estAi`・`planAi` |
| `desk-world-brief` | 毎日 7:17 | 世界ニュース（結果のある出来事と研究を8〜10本。予想の問い、媒体の見比べ、なぜを5回つき。指示は `desk/sync/world-brief-v2.md`、号の形は `WORLD_EDITION.md`） | `editions` |
| `desk-morning-mail` | 毎日 3:00・16:00 | Gmail と WhatsApp の個人トークの確認と返信案（`desk/whatsapp_sync.py`。送信しない） | `days`・`tasks`・`desk/data/whatsapp/` |
| `desk-minor-watch` | 火・金 7:33 | マイナー国ウォッチ | `watch` |
| `desk-claude-daily-report` | 毎日 7:40 | 前日の Claude の作業レポート | `reports` |
| `desk-money-upcoming` | 毎日 7:50 | カレンダーと予約メールから今後の支出（読むだけ。本文は残さない） | `desk/data/wise/upcoming.json` |
| `desk-money-tips` | 毎日 8:20 | ライフプランや世界の暮らしの豆知識を出典つきで1件 | `desk/data/wise/tips.json` |
| `desk-money-learn` | 毎日 8:35 | FP の6分野と金融機関のレポートから解説とクイズを1本 | `desk/data/wise/lessons.json` |
| `desk-money-review` | 3日ごと 8:10 | お金の助言と、新しい明細の種類・分類の案（`/money-review`） | `desk/data/wise/`（`advice.json`・`guess.json`） |
| `desk-sunday-mirror` | 日曜 17:08 | 日曜の鏡 | `memos` |

各タスクの手順は Mac の `~/.claude/scheduled-tasks/<ID>/SKILL.md` にあります。経歴やドキュメントの ID を含むので、このリポジトリには入れません。
