# 21 秘書ボット

写真の記録のボット（`lifeos/bot/`）とは別のボットです。Cloudflare Workers で動くので、Mac を閉じていても使えます。
Desk のデータには触りません。

## 仕組み

`src/index.js` の `fetch` が本人かどうかを確かめてキューに入れ、`queue` が処理します。`scheduled` が毎朝5時（22:00 UTC）に天気を送ります。

- 秘書・翻訳・文面：`src/assistant.js`（Claude API）
- カレンダーと Gmail：`src/google.js`（読むだけの権限）
- 天気と場所の名前：`src/weather.js`（Open-Meteo、OpenStreetMap）
- 保存：`src/store.js`（Cloudflare KV）

## 保存するもの

| 中身 | 期間 |
| --- | --- |
| 最後の位置（約1 km に丸める）と場所の名前 | 次に送るまで |
| 会話の文字（直近6往復）。メールの要約や訳も入る | 30分。「忘れて」ですぐ消える |
| 月の使用量 | 70日 |

メール本文と写真は保存しません。ログに本文は出しません。

## 守り

- 本人のチャット ID 以外は無視します。Webhook は秘密の文字列も確かめます。
- メールや予定を読んだあとは、同じ回の中で Web 検索を使えません。メールに隠れた指示で、中身を外へ出されないためです。
- リンクのプレビューは切っています。

## 導入

先に次の4つを用意します。

1. nodejs.org のインストーラで Node.js を入れる
2. Telegram の BotFather で新しいボットを作り、トークンを受け取る
3. Cloudflare のアカウントを作る
4. Claude Console で API キーを作り、使用上限も設定する

Terminal で実行します。キーは非表示の入力欄に打ちます。チャットには貼りません。

```bash
bash lifeos/21-assistant/scripts/setup.sh
```

Google をつなぐ手順です。

1. Google Cloud でプロジェクトを作り、Calendar API と Gmail API を有効にする
2. OAuth の同意画面を作り、公開ステータスを「本番環境」にする。「テスト」のままだと許可が7日で切れる
3. デスクトップアプリの OAuth クライアントを作り、JSON を `lifeos/21-assistant/client_secret.json` に置く（git に入らない）
4. 次を順に実行する。確認されていないアプリの警告は、自分のアプリなので進んでよい

```bash
cd lifeos/21-assistant
```

```bash
npx wrangler secret put GOOGLE_CLIENT_ID
```

```bash
npx wrangler secret put GOOGLE_CLIENT_SECRET
```

```bash
python3 scripts/google_auth.py client_secret.json | npx wrangler secret put GOOGLE_REFRESH_TOKEN
```

Google のパスワードを変えると、Gmail の許可は切れます。そのときは最後の行だけやり直します。

## 変えるとき

- モデルと月の上限：`wrangler.toml` の `MODEL`・`MONTHLY_LIMIT_USD`（`src/usage.js` に料金表）
- テスト：`npm test`
- 反映：`npm run deploy`
