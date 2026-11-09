# 返信のたたき台（reply-helper）

相手のメッセージを貼ると、丁寧で短い英語の返信案を3つ出すページ。
ライブコマースの職場で、高圧的な依頼をやわらかく断る・線を引くために作った。

- 返信案は3種類：やわらかく断る／はっきり線を引く／短く終える。
- それぞれに日本語の意味と「コピー」ボタンが付く。
- 入口はパスワード1つだけ。権限の違いはない。
- 貼った文章は保存しない（サーバーにもログにも残さない）。

## しくみ

- Cloudflare Workers で動く（無料枠で足りる）。
- ページとサーバーは `src/` の2ファイル。
- Claude API（`claude-opus-5-5`、effort は low）を呼ぶ。API キーはサーバー側だけに置き、ページには出さない。
- 断られたときの予備（`fallbacks: "default"`）を入れてある。

## 公開する手順（Mac で1回だけ）

1. Cloudflare のアカウントを作る（無料）。
2. このフォルダで準備する。

```bash
cd reply-helper
```

```bash
npm install
```

```bash
npx wrangler login
```

3. API キーとパスワードを入れる（聞かれたら貼る。ファイルには書かない）。

```bash
npx wrangler secret put ANTHROPIC_API_KEY
```

```bash
npx wrangler secret put APP_PASSWORD
```

4. 公開する。最後に出る `https://reply-helper.〇〇.workers.dev` を友達に送る。

```bash
npm run deploy
```

パスワードを変えるときは、手順3の2つ目をもう一度実行する。公開し直しは要らない。

## 費用の目安

- 1回あたり、入力 約1,500 トークン・出力 約1,000 トークン（考える分を含む）として、約4円（1ドル150円で計算）。実際の数字は Claude Console の使用量で確かめる。
- 料金はキーの持ち主（自分）に付く。Console で月の上限を決めておくと安心。
- パスワードが短いと、他人に使われることがある。友達以外に URL を広めない。

## 手元で試す

`.dev.vars`（git に入れない）に次を書いて `npm run dev`。

```
ANTHROPIC_API_KEY=本物のキー
APP_PASSWORD=好きな合言葉
```

`ANTHROPIC_BASE_URL` を足すと、偽の API に向けて画面だけ試せる（本番では入れない）。
