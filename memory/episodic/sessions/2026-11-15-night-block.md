# 2026-11-15 夜のブロック（FocusBlock）

## ひとこと
- いま：夜のSNSとBraveを止める
- 次：入れ直してから設定
- 待ち：sudo での入れ直し

## やったこと
- FocusBlock のスケジュールに専用の止めるリスト（`appList`・`siteList`）を足した。空なら今までどおり上のリストを使う。
- 理由：上のサイトのリストは「許可したもの以外を止める」型。夜にそれを使うと Desk の夜の自動処理（Telegram・Cloudflare など）の通信まで止まる。専用リストは指定したサイトだけを止める。
- daemon が止めた回数を数える（`attempts.json`、14日、同じものは1分に1回）。Desk の集中の画面に「Always on」の欄を足し、止めた回数と Chrome で開いた回数（Web履歴、10分あけて1回）を出す。
- Desk の「Unlock」は `scope:"focus"` を送り、専用リストのスケジュールは止めない。1日中のスケジュールは集中中と見なさない（`appsGlobal`・`webGlobal`）。
- 1日中のスケジュールは start と end を同じ時刻にする（`schedule_window` が翌日まで伸ばす）。

## 次の一手
- `sudo ./focusblock/install.sh` のあと、`/api/state` に `attempts` があるのを確かめてから2つのスケジュールを入れる：Night SNS（0:00〜5:00、SNS）と No Brave / YouTube（1日中、Brave Browser と youtube.com・youtu.be）。古い daemon に送ると専用リストが消えて全部止まるので、先に送らない。
