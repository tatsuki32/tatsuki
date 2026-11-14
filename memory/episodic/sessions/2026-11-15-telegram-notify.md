# 2026-11-15 タスクの Telegram 通知

## ひとこと
- いま：Telegram 通知を作った
- 次：ボットを作ってつなぐ
- 待ち：本人のボット作成

## やったこと
- `desk/tg_notify.py` を新しく作った。`server.py` の 4747 で60秒ごとに動く。1件1通。
- 種類：予定の10分前（時間帯なし）、追加・完了・削除、期限は明日・今日、期限超過（1日1回）、止まっている進行中（3日）、進行中（1日1回）、返信。
- タスクは Mac の時計で 4:00〜17:00。外の分は貯めて 4:00 から。1日30通まで、超えた分は1通で数だけ。
- 予定は Google カレンダーの iCal 非公開 URL を直接読む（`calendar/upcoming.json` の取り込みが止まっているため）。RRULE（DAILY・WEEKLY+BYDAY・MONTHLY・YEARLY、COUNT・UNTIL・EXDATE・RECURRENCE-ID）を自前で展開。
- 「🔔 お知らせ」画面に Telegram の欄。テスト `desk/tests/test_tg_notify.py`（12件）。

## 決めたこと
- 最初の1回は今の状態を覚えるだけで送らない（既存の全部が「追加」で届かないように）。
- 設定した時点で待っている返信は送らない。来たものだけ。
- 返信のタスクは追加・完了・期限の通知に出さず、「Reply needed」だけ。
- tasks.json が読めず空のときは前回の状態を残す（全部が削除扱いにならないように）。

## 次の一手
- 本人：BotFather でボットを作り、`security add-generic-password -a telegram -s tatsuki-desk-telegram-notify -w`。ボットに /start、Desk の Connect、Send a test。
- 本人：Google カレンダーの設定 → iCal 形式の非公開アドレスを `security add-generic-password -a gcal -s tatsuki-desk-gcal-ics -w`。
- 次フェーズ：Telegram のボタンで完了・明日に回す。iPhone で開けるリンク。
