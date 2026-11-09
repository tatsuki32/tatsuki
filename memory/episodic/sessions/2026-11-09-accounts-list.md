# 2026-11-09 アカウント一覧（仮）とサイトの引っ越し

## ひとこと
- いま：アカウント一覧の仮版ができた
- 次：鍵つきパスワード欄の要件定義
- 待ち：ログイン方法の確認（本人）

## やったこと
- ポートフォリオのサイトを Netlify から Cloudflare Pages に移した（手で上げる方式。上げたフォルダは `_upload/`、`.git/info/exclude` で Git の外）。`desk/data/showcase.json` の `siteUrl` を新しい URL にした。
- Desk に「🔑 Accounts」を足した（`desk/accounts.py`、`index.html` の `acView`・`wireAc`・`acOpen`、`server.py` の `accounts_write`）。口座の画面（`bank.py`）と同じ守り方：この Mac だけ、0600、GitHub の保管から外す。
- パスワードは持たない。`SECRET` の正規表現でキーや「password: …」を断る。前置きのない文字だけのパスワードは見分けられない。

## 分かったこと
- Desk の `index.html` には viewport の meta がない。ブラウザのスマホ幅の試しは、meta を一時的に足さないと 980px で描かれる。
- Cloudflare の画面は CSP で localhost からの fetch を止めるので、ファイルの受け渡しは本人のドラッグが要る。

## 次の一手
- パスワード欄を鍵つきで足す（`/desk-feature` で質問 → 要件定義 → 承認）。macOS のキーチェーンに置く案が有力。
