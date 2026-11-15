# 2026-11-15 メルマガ「地球キャンパス通信」

## ひとこと
- いま：メルマガの仕組みができた
- 次：Resend の登録と公開
- 待ち：送り主の住所を入れる

## やったこと
- 費用を比べた（まぐまぐは売上の50%、自作は Stripe の3.6%だけ）。まずは無料で自作に決めた。
- 題は「地球キャンパス通信」、副題「通信制大学生が、世界を歩いて人類を考える」。連載名「途上の人類」、一言「世界在籍」。
- 要件定義：https://claude.ai/artifact/N7ssVSA9EdxfHK21ZzpSvx
- `desk/campus.py`（号の保存・Markdown→メール・tany.im/campus のページ・Tidy up・送信の依頼）、`#campus` の画面、`server.py` の `/api/campus/...`。
- tany.im の Worker に `campus.js`（登録・確認・停止・送信・統計、D1 の表は初回に自動で作る）。Worker のファイルは `desk/data/tany/site/`（公開リポジトリの外）。

## 確かめたこと
- 手元の Worker（`wrangler dev --local`、送信は `CAMPUS_FAKE_SEND`）で：登録2件 → 同じアドレスの2回目と隠し欄は確認メールなし → ボタンで確認 → Send test → Send to readers で各自の停止リンク入り → 2回目の送信は0件 → 送った号は題を直せない → 1クリック停止。
- 375px：tany.im/campus/ と Desk の画面に横スクロールなし（Desk は viewport の meta がないので iframe で測った）。
- Tidy up は本物の API で1回（誤字だけ直し、口調は残った）。

## 残り
- 本人：Resend に登録して tany.im を足す（DNS に SPF・DKIM）。キーを `npx wrangler secret put RESEND_KEY`、合言葉を `CAMPUS_KEY` とキーチェーン `tatsuki-desk-campus` に。
- Worker の deploy（`cd desk/data/tany/site && npx wrangler deploy`）と、Desk の Settings に送り主の名前・住所・問い合わせ先・テスト先。
- Send test のメールでは写真が出ない（写真は本番の送信で tany.im に上がってから出る）。
- 次の段階：Post Studio へ回す、SNS のお知らせ、予約送信、開封数、有料版。
