# 2026-11-15 メルマガ「地球キャンパス通信」

## ひとこと
- いま：メルマガを送れる状態になった
- 次：第1号を書いて送る
- 待ち：なし

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

## 公開（同じ日の夕方）
- Resend に tany.im（東京）を登録。Cloudflare の DNS に4件（`send`・`rsend` の CNAME、`resend._domainkey` と `_dmarc` の TXT）。受信用の MX は入れていない（tany.im の受信は Cloudflare のメール転送のまま）。
- Worker の秘密 `CAMPUS_KEY`（キーチェーン `tatsuki-desk-campus` と同じ）・`RESEND_KEY`（Sending access、tany.im だけ）。deploy 済み。登録ページ tany.im/campus/ を公開。
- テスト送信が Gmail の受信箱に届いた（迷惑メールではない）。
- ハマりどころ：`wrangler secret put` に手で貼ると別の文字が入ることがあった。鍵はコピーのボタン → `pbpaste | tr -d '[:space:]' | npx wrangler secret put RESEND_KEY` で入れる。鍵が正しいかは `pbpaste` の鍵で `api.resend.com` を叩けば分かる（Sending access の鍵は 401 restricted_api_key が正常）。

## 載せる場所を広げた（同じ日）
- 仕事用プロフィール tatsuki.tany.im：日本語版だけの「出版物」にメルマガ（`PUB` の4つ目の値 `"ja"` で言語を絞る）。`bash sites/deploy.sh tatsuki`。
- プライベート用 tatsuki-about.tany.im（元は `desk/data/manual/public/`、Worker `tatsuki-about`）：連絡・SNS のシートと最後の段落にリンク。
- tany.im：トップにお知らせとは別の「メルマガ」欄、メニューに「メルマガ」「代表」、代表ページ `/founder/`（本文はプロフィールの事実だけ。site フォルダに置く）。一覧は `campus._lists()` が送信のたびに差し替える。
