# 個人のサイトを tany.im のサブドメインへ

## ひとこと
- いま：ポートフォリオを tatsuki.tany.im に移した
- 次：ほかの pages.dev も短い名前で移す
- 待ち：なし

## やったこと
- 個人のものは `<短い英単語>.tany.im` に置く決まりにした。一覧と手順は `sites/README.md`、場所は `sites/sites.json`。
- ポートフォリオを Worker（静的ファイル）`tatsuki-site` として `tatsuki.tany.im` に公開。`custom_domain = true` で DNS は自動で作られる。
- 元ファイルは経歴入りなのでリポジトリの外のまま。`bash sites/deploy.sh <名前>` が一時フォルダ `sites/<名前>/build`（git の外）に写して `wrangler deploy` する。wrangler は `sift/node_modules` のものを使う（サンドボックスの外で実行が要る）。
- 旧 `tatsuki3284.pages.dev` は消していない（Pages プロジェクトはそのまま）。Desk の見せる素材の `siteUrl` は新しい URL に変えた。

## 残り
- `tatsuki-routine.pages.dev`（Functions と KV を使うので Worker への書き換えが要る）、`tatsuki-slots.pages.dev` も移せる。
