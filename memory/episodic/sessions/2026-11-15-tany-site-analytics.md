# 2026-11-15 会社のサイト（tany.im）に計測を入れ、Desk の「📈 Site」で見られるようにした

## ひとこと
- いま：tany.im の計測と Desk の画面が本番で動いている
- 次：数日たまってから数字を見て直す
- 待ち：Search Console の数字（2〜3日後から）

## やったこと
- 要件定義：https://claude.ai/artifact/CBgps2tDguZZ5UULqBZJ4f（Cookie＋同意バナー、録画あり、Search Console、Desk の新画面＋Home 1行）。
- サイト（`desk/data/tany/site/`、git の外）：`public/t.js`（計測と同意バナー）、`public/privacy/`、`sitemap.xml`、`robots.txt`、Google の確認ファイル。`worker.js` に `/_t`（受け口）・`/_t/export`（取り出し口）・クローラーと 404 の記録・毎日の削除。D1 `tany-analytics`（`schema.sql`）。
- Desk：`desk/site_sync.py`（取り込み・集計・Search Console・SEO 点検・UTM）、`server.py` の API、`index.html` の `#site`（Overview・Content・Conversion・UX・SEO）と Home の1行。
- 外の設定：Search Console に `https://tany.im/`（HTML ファイル）とサイトマップを登録。Google Cloud「Tatsuki Desk」で Search Console API を有効化し、読むだけの許可。Clarity のアカウントとプロジェクト `zdnj0id8gz`。
- 本番で確かめた：同意前は Cookie なし、許可で `_tv`・`_ts`・Clarity、送信は 204、別サイトからは 403、合言葉なしの取り出しは 404、www の 301 はそのまま。Desk に漏斗4段・問い合わせ・事例の時間・Web Vitals が出た。試験の記録は D1 と Mac の両方から消した。

## つまずき
- `npx` は `~/.npm` の権限エラーで動かない。`npm_config_cache=$TMPDIR/npmcache` を付ける。
- Cloudflare の静的ファイルは `*.html` を拡張子なしへ 307 で送る。Google の確認ファイルは Worker が直接返す。
- Desk のページには viewport の指定がないので、ブラウザのスマホ表示は 980px になる。375px の確認は幅 375px の iframe で開いて見る。
- Desk の CSS の `.srow`・`.sgrid` はすでにタスクで使われていた。新しい部品は `st` で始まる名前にした。
- 同じファイルに別の作業の未コミットの変更があったので、HEAD に自分の変更だけを当てたものを `git update-index --cacheinfo` でステージした。
