# 2026-11-15 夜のブロック（FocusBlock）

## ひとこと
- いま：夜のSNS止めを作った
- 次：本人が入れ直して設定
- 待ち：sudo での入れ直し

## やったこと
- FocusBlock のスケジュールに専用の止めるリスト（`appList`・`siteList`）を足した。空なら今までどおり上のリストを使う。
- 理由：上のサイトのリストは「許可したもの以外を止める」型。夜にそれを使うと Desk の夜の自動処理（Telegram・Cloudflare など）の通信まで止まる。専用リストは指定したサイトだけを止める。
- 入っている daemon は古いので、`sudo ./focusblock/install.sh` のあとに 0:00〜5:00 のスケジュールを入れる。古い daemon に送ると専用リストが消えて全部止まるので先に入れない。

## 次の一手
- 入れ直しのあと、スケジュール「Night SNS」（毎日 0:00〜5:00、アプリ Brave Browser、サイト SNS と youtube.com）を `/api/settings` で足す。
- サイトを開いた回数の表示は `/desk-feature` で要件から。
