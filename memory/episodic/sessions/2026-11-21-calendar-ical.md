# 2026-11-21 カレンダーの取り込みを iCal に

## ひとこと
- いま：予定の取り込みを直した
- 次：iCal の住所を入れる
- 待ち：本人の住所の登録

## やったこと
- Desk のカレンダーが 11/5 から止まっていた原因：予定を取り込んでいた毎時の定期タスク `desk-calendar-gtasks` が 11/5 に消え、代わりがなかった。
- `desk/gcal_ics.py` を追加。サーバー（4747）が5分ごとにキーチェーン `tatsuki-desk-gcal-ics` の iCal アドレスを読み、今日から8日分を `docs/calendar/upcoming.json` に書く。iCal の読み取りは `tg_notify.py` の部品（unfold・parse_dt・occurrences）を使い、終わりの時刻と場所を足した。
- 試験 `desk/tests/test_gcal_ics.py`（繰り返し・EXDATE・移動した回・終日・取り消し・☑️・前日から続く予定）。
- 同じ日、Desk のデータでトビタテを OKR に組み替え、プロジェクト「TANY LLC setup」「VinePro Academy」を OKR つきで作った（コードは変えていない）。

## 残り
- 本人が Google カレンダーの設定から iCal の非公開アドレスをキーチェーンに入れる。入れるまで取り込みは動かない。
- 「Desk Log」カレンダーがまだない（`desk-time-calendar` が書けない）。
- VinePro のボード（VINECK）の通知を毎朝拾う仕組みは方式の決定待ち。
