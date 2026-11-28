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

## 続き：会議を短く
- 目的の長さを 5・10・14・19・24分に（Quick question を足した）。Desk の Bookings から本番に保存済み。
- 相手の入力欄に「先に共有して」の1行と「先に見ておく資料」の欄。予定の説明欄は「Materials / notes」。
- 次の候補：会議の前に文字で相談する道（テンプレを埋めてメールで問い合わせ）。要件の承認待ち。

## 続き：会議の代わりに書いて送る・進め方の PDF
- `book.js` に `WRITE`（4言語）・`apiWrite`・相手の画面の型とプレビュー。本番に公開、BAD_BODY の返事まで確認（本物の送信はしていない）。
- `desk/book_guide.py`：Drive の「会議の考え方」から相手向けに縮めた PDF（日本語の見本）。PDF は `wise_report.to_pdf`（サンドボックスの中では作れない）。
- 残り：PDF の公開場所と、予約の確認メールにリンクを足すこと。英・露・越の版。

## 続き：PDF を4言語で公開・テンプレート
- `book_guide.py` に en・ru・vi。PDF は tany.im/guide/meetings-<言語>.pdf（Worker が先に動くので /book/ の下には置けない）。
- 確認メール（booked・moved）と完了画面にリンク。
- `book_templates.py` → Drive に6つの Google ドキュメント（HTML を text/html で上げると変換される）。フォルダをリンク共有（閲覧）。

## 続き：テンプレートを言語ごとに分けた・Google の接続切れ
- 英日を混ぜた6つはゴミ箱へ。言語ごとのフォルダに6つずつ（24）。PDF のリンクも言語ごとのフォルダに。
- 20時台に「いま予約の受付を止めています」：Google の許可が 17:11 に切れていた（11/20 に取った許可が7日で切れた。テスト中のアプリの許可の期限と同じ日数）。予約の瞬間に予定を作れず DOWN が返り、画面がその表示に変わる。Desk の Connect Google Calendar でつなぎ直しを始めた（本人の Allow 待ち）。

## 予約が止まった件（22:50）
- 原因：Google の許可（11/20 発行）が7日で切れた（17:11 NOT_CONNECTED）。予約の確定で予定を作れず DOWN → 「受付を止めています」。
- 直し：Desk の Connect Google Calendar で取り直し。google.ok true、カレンダー一覧の取得も成功。アプリは公開済みなので次は7日で切れない見込み。
- 残り：ページからの本物の予約1件はまだ（本人のアドレスで試すため）。

## 11/28 本人への通知と予定の題
- 予約が入っても本人にはメールが来なかった（相手向けの確認メールだけだった）。`ownerMail` を足し、予約・変更・相手の取り消しで ADMIN_EMAIL に届く。
- 予定の題を「名前 × Tatsuki MTG」に（設定の title を本番も変更）。本番で Resend の試験アドレスで1件予約 → 通知メールと新しい題を確認 → 取り消し。
