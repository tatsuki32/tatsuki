# 2026-11-05 タスクをやりやすい形に整える機能

## ひとこと
- いま：タスクを夜に整えられる
- 次：朝に結果を見る
- 待ち：サーバーの再起動

## やったこと
- `desk/task_clarify.py`：`pending`（答え→今日→今週→古い順、30件）と `apply`（形と長さを検査し `clear` だけ書く）。
- `desk/index.html`：詳細に「まず5分で」「誰とやる」、手順と出典は畳む。質問カード `openQuestions`。一覧は `taskName` で書き直しを出す。
- `desk/server.py`：`keep_clear`（古い画面の保存で `clear` が消えない）。`/clarify` と `/split` を許可。
- `.claude/commands/clarify.md`、定期タスク `desk-task-clarify`（毎晩 2:30）。
- テスト：`lifeos/tests/test_desk_task_clarify.py`。

## 決めたこと
- 公開リポジトリなので、画面とコードの言葉は「やりやすくする」「整える」にする。文体の基準は `desk/data/clarify-style.md`。
- 元の `title` は上書きしない。カレンダーとできたことは元の文のまま。
- 毎日増える `lifeos-19`、メール、`daily` は対象外。

## 気づいたこと
- `server.py` の変更は、常駐サーバーの再起動まで効かない。`index.html` は再読み込みで効く。

## 残り
- `lifeos/tests` の失敗2件（test_14 の bot、test_11 の定期タスク説明）は今回の変更とは別。
