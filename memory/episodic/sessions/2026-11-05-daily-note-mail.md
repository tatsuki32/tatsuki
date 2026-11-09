# Daily Note メール

## ひとこと
- いま：メールを送る仕組みができた
- 次：16時の定期タスクを登録する
- 待ち：本人の登録と試しの返信

## やったこと
- `lifeos/19-daily-note/`（core・cli・tool.json）と `lifeos/tests/test_19_daily_note.py` を追加。21件通る。
- 定期タスク `desk-daily-note` の手順を追加。毎時の `desk-calendar-gtasks` に返信確認を追加（どちらも `~/.claude/scheduled-tasks/`）。
- 実メールで1回送信。Desk のカードと送信記録ができることを確認。

## 決めたこと
- 返信の保存先は当日のタスクの `note`。新しい画面は作らない。
- カードは日ごとに新規（`source: lifeos-19`、`sourceRef` が日付）。id は `tasks.add` の自動のものを使う。
- 返信は `SENT` ラベルかつ送信元が一致するものだけ取り込む。
- 前日の未返信は次の毎時確認で `archived` へ。3日以内の返信で `done` に戻す。

## 残り
- 16時の定期タスクの登録は本人が Claude アプリで行う。
- 返信の取り込みは実メールで未確認。
- `test_14_lifemap` の `test_pass_on_the_bot_clears_the_desk` が失敗する。この作業では 14 番に触っていない。
