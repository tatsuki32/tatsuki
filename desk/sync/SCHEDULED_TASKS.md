# Tatsuki's Desk の定期タスク（Mac の Claude スケジュールタスク）

言語（2026-11-09 から）：どのタスクも Desk に書く文はアメリカ英語。各 SKILL.md の本文の先頭に「Language (changed 2026-11-09)」の段落がある。`*_ja` の欄は名前のままで中身は英語。 メールと連絡は原文を残して英語訳を添え、返信の下書きは相手の言語（日本語のメールには日本語）。日本語の文を直すときは日本語のまま。カレンダーの予定は訳さずそのまま出す。今あるタスクの題は日本語のまま（改名しない）で、これから作るタスクは英語。

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

- 使えるコレクション：`editions`、`days`、`watch`、`reports`、`calendar`、`gtasks`、`tools`、`memos`、`tasks`、`notes`、`habits`、`wins`、`presents`、`jobapps`。
- `memos` や `tasks` はサーバー経由で書きます。`desk/data/*.json` を手で書き換えません。
- メール本文やメモの中身を、報告やログに書きません。一時ファイルは使い終わったら消します。

## タスク一覧

| ID | 時刻（ホーチミン） | 内容 | 書く先 |
|---|---|---|---|
| `desk-daily-note` | 毎日 16:00 | 3min Daily Note のメールを本人へ送り、当日のタスクを作る（`lifeos/19-daily-note/`） | `tasks` |
| `desk-lifelog-drive` | 毎時 | Drive の `Daily_Photos_Inbox` の新しい写真を待ち行列に入れ、読む（`desk/lifelog.py`。Drive のファイルは動かさない。1回10枚まで） | `lifelog/`・`body/`・`wise/receipts.json` |
| `desk-sleep-coach` | 毎日 7:00 | 睡眠の記事を決めた出典から1本読み、本人の記録に当てはめた助言を作る（`desk/lifelog.py`。医療の助言はしない） | `body/sleep_coach.json` |
| `desk-task-calendar` | 停止中（2026-11-14〜） | 前は未着手で時刻つき期日のタスクを Google カレンダーへ（`desk/task_calendar.py`）。今は Desk の「Add to calendar」で1件ずつ | `calendar-sync.json` |
| `desk-task-clarify` | 毎晩 2:30 | 未整理のタスクを30件まで、具体的なタイトル・まず5分・手順・出典・頼む文面に整える（`desk/task_clarify.py`。送信しない） | `tasks` の `clear` |
| `desk-task-estimate` | 毎日 6:40 | タスクにかかる時間の目安と時期の案（`desk/task_estimate.py`。本人の選択は変えない） | `tasks` の `estAi`・`planAi` |
| `desk-world-brief` | 毎日 7:17 | 世界ニュース（結果のある出来事と研究を8〜10本。予想の問い、媒体の見比べ、なぜを5回つき。指示は `desk/sync/world-brief-v2.md`、号の形は `WORLD_EDITION.md`） | `editions` |
| `desk-morning-mail` | 毎日 4:30・15:30（2026-11-15 に 3:00・16:00 から。返信の時間 5:00・16:00 の前にそろえる） | Gmail と WhatsApp の個人トークの確認と返信案（`desk/whatsapp_sync.py`）。相手の返信待ち（`lifeos/02-followup/MORNING_RUN.md`、`run.py whatsapp`）。プレゼントの判定（`desk/presents.py`）。今すぐ返信が要るものに `urgent` の印（Desk の通知が鳴る。`desk/notify.py`）。送信しない | `days`・`tasks`・`desk/data/whatsapp/`・`02-followup/state.json`・`presents` |
| `desk-task-skip` | 毎日 2:45 | タスクに「やらなかったらどうなるか」・影響小の印・種類・ジャンルを1晩40件まで（`desk/task_skip.py pending|apply`）。本人が直したものは触らない。期日つき・お金・手続き・健康・約束は影響小にしない | `tasks`・`genres` |
| `desk-task-tidy` | 月曜 6:15 | ジャンルの見直し（Claude の作ったものだけ、100個以内。`task_skip.py genre-review|genre-apply`）と、同じジャンルの似たタスクをまとめる案を10件まで（`merge-pending|merge-add`）。まとめるのは本人がカードで決めたときだけ | `genres`・`mergeprops` |
| `desk-time-calendar` | 毎日 23:30 | 7日以内に終わった5分以上の計測を Google カレンダー「Desk Log」に入れる（`desk/time_calendar.py pending|mark|status`）。題と時間だけ、空き時間・通知なし、二重に入れない。Desk Log が無ければ書かずに `status missing` | `timelog` |
| `desk-minor-watch` | 火・金 7:33 | マイナー国ウォッチ | `watch` |
| `desk-joins-weekly` | 金 15:00 | JOINS の新着案件メールから、リモートで働ける案件だけに応募フォームの欄ごとの下書きを作る（2026-11-14〜。Skill Shift と毎日の求人チェックのタスクは終了）。経歴はホームページの元データと `prompts` の事実だけ。応募・入力・送信はしない。やったことは `by:"claude"` のタスク（本人の一覧には出ない） | `jobapps`・`tasks` |
| `desk-heritage-daily` | 毎日 7:05 | 毎日世界遺産。まだ出ていない国を3カ国、国の紹介と遺産を写真つきで（`desk/heritage.py`。休んだ日の分は作らない） | `heritage`・`desk/data/heritage/photos/` |
| `desk-claude-daily-report` | 毎日 7:40 | 前日の Claude の作業レポート | `reports` |
| `desk-money-upcoming` | 毎日 7:50 | カレンダーと予約メールから今後の支出（読むだけ。本文は残さない） | `desk/data/wise/upcoming.json` |
| `desk-money-tips` | 毎日 8:20 | ライフプランや世界の暮らしの豆知識を出典つきで1件 | `desk/data/wise/tips.json` |
| `desk-money-learn` | 毎日 8:35 | FP の6分野と金融機関のレポートから解説とクイズを1本 | `desk/data/wise/lessons.json` |
| `desk-money-review` | 3日ごと 8:10 | お金の助言と、新しい明細の種類・分類の案（`/money-review`） | `desk/data/wise/`（`advice.json`・`guess.json`） |
| `desk-sunday-mirror` | 日曜 17:08 | 日曜の鏡 | `memos` |
| `desk-idea-suggest` | 月曜 6:30 | 提案。Drive と Cosense に本人が書いたものから、作れるものを5件まで。本人の言葉の引用と出典つき（`desk/ideas.py`。読む範囲は `desk/data/ideas/sources.json` だけ。引用が原文にないと保存しない。読んだ本文は残さない） | `ideas` |

各タスクの手順は Mac の `~/.claude/scheduled-tasks/<ID>/SKILL.md` にあります。経歴やドキュメントの ID を含むので、このリポジトリには入れません。
