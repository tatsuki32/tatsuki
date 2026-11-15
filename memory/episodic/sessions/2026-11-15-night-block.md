# 2026-11-15 夜のブロック（FocusBlock）

## ひとこと
- いま：夜SNSとBraveを止めた
- 次：数日使って回数を見る
- 待ち：なし

## やったこと
- FocusBlock のスケジュールに専用の止めるリスト（`appList`・`siteList`）を足した。空なら今までどおり上のリストを使う。
- 理由：上のサイトのリストは「許可したもの以外を止める」型。夜にそれを使うと Desk の夜の自動処理（Telegram・Cloudflare など）の通信まで止まる。専用リストは指定したサイトだけを止める。
- daemon が止めた回数を数える（`attempts.json`、14日、同じものは1分に1回）。Desk の集中の画面に「Always on」の欄を足し、止めた回数と Chrome で開いた回数（Web履歴、10分あけて1回）を出す。
- Desk の「Unlock」は `scope:"focus"` を送り、専用リストのスケジュールは止めない。1日中のスケジュールは集中中と見なさない（`appsGlobal`・`webGlobal`）。
- 1日中のスケジュールは start と end を同じ時刻にする（`schedule_window` が翌日まで伸ばす）。

## 結果
- 入れ直し済み。Night SNS（0:00〜5:00）と No Brave / YouTube（1日中）を入れた。youtube.com は止まり、google.com は通る。止めた回数も数えている。
- Desk の「Always on」の欄は、Desk のサーバーが次に再起動したあとに出る。

## 5分だけ使う（同じ日の続き）
- Mac のログインパスワードを3回入れると、サイトだけ5分開く（`/api/pass`、`CFG["pass"]`）。アプリは止めたまま。終わると自動で戻る。「Block again」で早く戻せる。
- パスワードの確認は PAM（サービス `chkpasswd`、`/usr/lib/libpam.2.dylib` を ctypes で）。dscl は引数にパスワードが出るので使わない。3つが違う・間違いは30秒待ち。
- Desk の集中の画面と FocusBlock の設定画面にボタン。今日使った回数を出す。
