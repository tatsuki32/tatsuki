# 2026-11-15 tany.im/admin（自分用ツールの入口）— 途中で保留

## ひとこと
- いま：入口ページとサーバー側の本人確認を作って公開。ログイン（Cloudflare Access）は本人の設定待ちで、本人の判断で一旦保留
- 次：Desk のタスク「Finish the sign-in for tany.im/admin (Cloudflare Access)」（id `t-tany-admin-login`）に手順あり
- 待ち：本人が Zero Trust を有効にして、チーム名と AUD タグを渡すこと

## やったこと
- `desk/data/tany/site/public/admin/index.html`：ツールのタイル（Routine・地図・Sift・go.tany.im・tany.im・Desk）。足すときはページ内の `TOOLS` に1行。
- `desk/data/tany/site/worker.js` の `admin()`：`/admin` は Access の署名（`Cf-Access-Jwt-Assertion`、RS256、チームの certs）を確かめ、`ADMIN_EMAIL` 本人だけ通す。`ACCESS_TEAM`・`ACCESS_AUD`（`wrangler.jsonc` の vars）が空のうちは誰も入れない（403）。偽の署名・期限切れ・別のメール・別の AUD で閉じることを試した。`robots.txt` で `/admin` を外した。公開済み。
- 本人の選択：ログインは Gmail に届くコード（Access の One-time PIN）、場所は `tany.im/admin`、Routine と地図の pages.dev も同じログインで閉じる。Routine の `/api/sync` は Bypass（合言葉で守る）。
- wrangler の OAuth には Access を変える権限がないので、Zero Trust と Access のアプリは本人がダッシュボードで作る。

## 決めたこと
- 自前の ID とパスワードは作らない（Cloudflare Access に任せる）。
- 本人のメモ：Routine アプリやログイン付きの入口は、ほかの人向けのサービスにもできそう。
