# 2026-11-14 会社のサイト（tany.im）に開発事例を10件載せた

## ひとこと
- いま：tany.im に開発事例10件を公開した。www の転送も戻した
- 次：事例の言葉を使いながら直す。数字は出典があれば足す
- 待ち：なし

## やったこと
- サイトの元のファイルが手元になかったので、公開中のページから作り直した。置き場所は `desk/data/tany/site/`（git の外）。
  - `public/index.html`：ページ本体。ほかに favicon.svg、apple-touch-icon.png、og.png。
  - `worker.js`：www.tany.im を tany.im に 301 で転送する（パスと `?` 以降は残す）。それ以外は `env.ASSETS` からファイルを返す。
  - `wrangler.jsonc`：`main: worker.js`、`assets.directory: ./public`、`binding: ASSETS`、`run_worker_first: true`。
  - `index.live.html`：直す前の公開版の控え。公開しない。
- `#made` の欄を開発事例のカードにした（2列。820px 以下で1列）。1枚に、誰の → The problem → Why it matters → What we built → For your company（会社ごとの合わせ方）→ 技術、の順で書く。
- 載せた10件：集中ブロック、サイトの滞在時間、ルーティン、Desk、Replies、回線の速さ、録音と文字起こし、お金、予約ページ、場所の地図。
- 「どれくらい必要か」に数字は入れていない（出典がないため）。困る理由を言葉で書いた。
- 確認：1200px と 375px（375px で横スクロールなし）。公開後のサイトで、10件・アイコン・共有の画像・www の 301 を確かめた。
- プレビュー：`.claude/launch.json` の `tany-site`（4795 番）。サーバーは `site/` を配信するので、ページは `/public/index.html` で開く。

## つまずき
- 公開の手順：`desk/data/tany/site` で `npx wrangler deploy` を実行する。リポジトリの一番上で実行すると、wrangler が `wrangler.jsonc` を勝手に作って失敗する。作られたファイルは消した。
- 前の Worker はスクリプト API で上げられていた。上書きすると、その中にあった www の転送も消えた。`worker.js` で作り直した。
- robots.txt は Cloudflare 側が付けるもので、サイトのファイルではない。
- 待っている確認の質問には、外からキーを送れない。`yes | npx --yes wrangler deploy` で答えを渡した。
