# 2026-11-20 プロフィールサイトにメルマガ登録欄

## ひとこと
- いま：自己紹介ページに登録欄を作った
- 次：OK が出たら公開する
- 待ち：公開してよいか返事をする

## やったこと
- プロフィールサイトの元ファイル（リポジトリの外。場所は `sites/sites.json` の `src`）の自己紹介カードの下に「Newsletter / メルマガ」カードを足した。
- フォームは会社サイトの登録ページと同じ `/campus/subscribe` へ直接 POST（`from=profile`、隠しの `website` 欄も同じ）。確認メールと二重の登録確認はそのまま効く。
- 文字は `UI` の `hNews`・`newsLead`・`newsPh`・`newsBtn`・`newsNote`・`newsMore`（英・日。他の言語は英語に戻る）。
- `.claude/launch.json` に `profile-preview`（4801、元フォルダを `http.server` で配信）を足した。375px と 1200px、日英で確認。

## 決めたこと
- Worker は変えない：`/campus/subscribe` は Origin を見ないので、別ドメインのフォームから送れる（Origin を見るのは計測の `/_t` だけ）。
- 本物のアドレスでの登録は試していない（確認メールが実際に送られるため）。

## 中断した場所
- 公開前。元フォルダは git の外なので、変更前のコピーは一時フォルダにだけある。

## 次の一手
- 本人の OK のあと `bash sites/deploy.sh tatsuki` で公開し、公開版で登録欄が出るか見る。
