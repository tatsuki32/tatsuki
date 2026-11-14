# 2026-11-14 求人チェックを JOINS だけにして Claude に任せた

## ひとこと
- いま：JOINS の応募文を Claude が週1で作る
- 次：本人が下書きを見て自分で応募
- 待ち：金曜15時の初回の自動実行

## やったこと
- 毎日の求人チェックのタスク（`daily-jobs-<日付>`、`linkedTasks`）を出さないようにした。Skill Shift は終了。`PROMPTS` は JOINS だけ。
- Desk に「🤝 Done by Claude」（`#aidone`、`aiView`・`wireAi`）を追加。`by:"claude"` のタスクは `S.board` から外し、ここに並ぶ。`taskRecord` は `by` を残す。
- 新しいコレクション `jobapps`（`server.py` の `COLLECTIONS`、`sync.py` の `TWO_WAY`）。応募文は欄ごとに Copy、「I applied」「Skip」「Undo」。
- 定期タスク `desk-joins-weekly`（金 15:00）。JOINS のメールと公開一覧から、リモート可で期限内の案件だけ。経歴はホームページの元データ（`<script>` の中の日本語）と `prompts` の `joins` の事実だけ。送信しない。

## 気づいたこと
- JOINS の新着メールは金曜の昼（日本時間）に来る。リモート可は少ない（11/14 時点で期限内2件のうち1件）。
- 応募フォームはスクリプトで描画され、欄名と字数は WebFetch で読めない。詳細ページの「ご応募の際にお聞きしたいこと」が欄の代わりになる。
- ホームページ（pages.dev）も本文が JS 描画で、WebFetch では読めない。元ファイルの `<script>` から取り出す。

## 次の一手
- 初回の自動実行（11/20 金）の結果を「Done by Claude」で確かめる。

## 追記（同じ夜）：応募の送信も Claude が
- 八代目儀兵衛には本人の OK をもらって Claude が送った（受付メールあり）。フォームは HubSpot で、ページの中に直接ある（欄の name は `/joins-apply` の表を参照）。
- 「Send for me」ボタン（`data-jasend`）→ `POST /api/claude` の `/joins-apply <id>` → `claude --chrome` で Terminal に起動（`CLAUDE_CHROME`）。押すことがその1件の承認。基本の値は `desk/data/joins-apply.json`（git に入れない）。
- 存在しない id で起動まで確かめた。本物の送信は次の案件で。
