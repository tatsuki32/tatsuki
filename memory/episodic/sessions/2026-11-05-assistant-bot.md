# 秘書ボット（Life OS 21）

## ひとこと
- いま：秘書ボットを実装した
- 次：本人がボットと各アカウントを用意
- 待ち：導入と本番の確認

## やったこと
- `lifeos/21-assistant/` に Cloudflare Workers のボットを作った。秘書（予定・Gmail・Web）、翻訳と文面づくり、朝5時の天気。
- 流れは webhook → Queue → 処理。Workers Free は `waitUntil` が30秒までで、Claude の処理に足りないため。Queue の処理は15分まで。
- メールか予定を読んだ回は Web 検索を外す。メール内の指示で中身を外へ出されないため。リンクのプレビューも切った。
- モデルは `claude-opus-5-5`、effort は low。`wrangler.toml` で変えられる。

## 確かめたこと
- `npm test` 27件成功（通信はすべて偽物）。`wrangler deploy --dry-run` 成功。
- 本物の Claude API、Telegram、Google、Cloudflare には未接続。特に `tool_choice: none` と Web ツールの組み合わせは本番で確かめる。
- `lifeos/tests` の失敗2件（test_14 の skipped、test_11 の SCHEDULED_TASKS）は 21 を外しても出る。今回の変更とは別。

## 分かったこと
- Node.js はこの Mac になかった。テスト用は scratchpad に展開した。本人の配置には別に入れる必要がある。
- `@anthropic-ai/sdk` 1.5 は API キーを Authorization ヘッダーで送る。
- Google の同意画面が「テスト」のままだと許可は7日で切れる。
