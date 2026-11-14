# 2026-11-15 Sift Daily（登録した RSS のまとめとメール）

## ひとこと
- いま：sift.tany.im に公開した
- 次：合言葉とメールの設定
- 待ち：Email Routing の確認

## やったこと
- 止めた World Desk の毎朝の号（使用量の約4割）の代わりに、登録したサイトの RSS だけを読む仕組みを作った。
- 本人の希望で Desk の外の Web アプリにし、売れる形（紹介・料金・管理ページ）にした。名前は Sift Daily。
- `sift/`：Cloudflare Worker `tatsuki-sift`、D1 `sift`、cron 07:30 UTC（取得＋Haiku の要約）・08:00 UTC（メール）。
- RSS と Atom はライブラリなしで正規表現で読む（Workers に DOMParser がない）。URL から RSS を探す（link タグ → よくある場所）。
- メールは `send_email` に自分で組んだ MIME（multipart/alternative、base64）。
- Desk へは `desk/news_sync.py` が5分ごとに「Make it a task」を取り込む。

## 決めたこと
- 取得はコード、AI は1人1日1回の要約だけ。記事の本文は取らない。
- メールの Later・Task は署名つきリンク＋確認ボタン（メールの下見で勝手に押されない）。
- 料金は Free と Pro $4/月の2段。登録受付と支払いは、規約・支払いの会社・利用者向けのメール送信を決めてから。

## ハマりどころ
- `Referrer-Policy: no-referrer` だと、フォームの POST の Origin が `null` になる。同じサイトかを確かめる処理が落ちるので `same-origin` にした。
- wrangler dev は `localhost` に別のサーバーがいると、ブラウザがそちらに行く。`127.0.0.1` で開く。サンドボックスではポートを開けない。
- Workers の無料枠は外への通信が1回50まで。1回の取得は40サイトまで。

## 残り
- 本人：`bash sift/setup-secrets.sh`、tany.im の Email Routing を有効にして Gmail を宛先として確認。
- AI の要約は本番の初回の実行で確かめる（手元ではキーを使わなかった）。
