# 2026-11-27 予約ページの「Only times I pick」を旧 Today Slots の仕様に戻す

## ひとこと
- いま：緑はカレンダーより優先、赤で閉じる、期間を選べる
- 次：本人の画面で赤と期間を試してもらう
- 待ち：なし

## 原因
- `book.js`・`bookadmin.js` は Git の外（`desk/data/tany/site/`）なので、消えたコミットはない。
- 11-20 の置き換え（Today Slots → tany.im/book）と、11-27 午前の Open times の移植で、旧版の次の3つを移していなかった：
  「開ける（予定があっても可）」、「閉じる（守りたい時間）」、見せる期間のボタン（今日だけ〜来週まで）。
- そのため手で選んだ枠からも freeBusy を引いていた。終日の予定（12/1・12/2）が freeBusy では一日中「busy」になり、枠が全部消えた。

## 直したこと
- `cleanPicked` に `blocks`・`days`。`openTimes` は on のとき freeBusy を呼ばず、Tany の予約・前後の空き・notice・1日の上限・赤だけを引く。赤は Weekly rules でも引く。
- 管理画面：「Guests can book」の期間、Open (green) / Block (red) の筆、凡例、文言。Clear day は赤も消す。
- 旧版の画面は Pages の転送前の版（`17654cfb.tatsuki-slots.pages.dev/admin`）で見られる。比べるときはここ。
- 写し：`.backup/book.2026-11-27b.js`・`bookadmin.2026-11-27b.js`（直す前）。
- 本番：deploy 済み。`/book/api/slots` に 12/01 10:30–12:00 が出るのを確認。
