# 予定の3分前アラーム（2026-11-17）

## ひとこと
- いま：予定の3分前に鳴る
- 次：iCal の住所を入れる
- 待ち：本人の1手順

## やったこと
- `desk/event_alarm.py`：Google カレンダーの予定の3分前に、Mac の通知（音つき）＋閉じるまで残る警告の窓＋音4回＋題の読み上げ。時間帯・一時停止・集中に関係なく鳴る。1つの予定は1回だけ（`desk/data/notify/alarm.json`）。
- 予定は `tg_notify.upcoming()`（キーチェーン `tatsuki-desk-gcal-ics` の iCal を5分ごと。無ければ Desk の取り込み）。終日と ☑️ は鳴らさない。
- `server.py` の 4747 だけで20秒ごと。`python3 desk/event_alarm.py plan|test`。

## 残り
- Desk の取り込み（`upcoming.json`）は11日前で止まっている。iCal の非公開アドレスをキーチェーンに入れるまで、新しい予定では鳴らない。
