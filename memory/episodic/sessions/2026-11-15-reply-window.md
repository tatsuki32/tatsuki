# 2026-11-15 返信の時間と、返信をタスクから外す（要件定義 B）

## ひとこと
- いま：返信は Replies だけ。5時と16時に開く
- 次：やらないと決める（A）と種類・ジャンル（F）
- 待ち：本番で 16:00 に開くか

## やったこと
- 返信の判定を1か所に：`replyKind`（index.html）と `desk/replywin.py` の `reply_kind`。`mail`・`whatsapp`・`line`・`lifeos-02`（催促）と、題が `Email reply`／`Email nudge`／`メール返信`／`WhatsApp催促` などで始まるもの。`asTask:true` は本人の選択なので Tasks に残す。
- 実データで確認：返信41件（メール12・催促24・LINE 4・WhatsApp 1）が Tasks から Replies に移った。催促の25件は今まで Tasks に出ていた。
- 返信の時間：Mac の時計の 5:00・16:00 に開く。「Replies done」で `replywin` の doc `state` に `closedAt`。閉じている間は題・件数・タブの件数・検索・タスクの詳細・メールの音・`notify.py` の急ぎの通知を出さない。閉じると `wins` に `replies-<日付>-am|pm`（種類 `replies`）。
- `kickoff.py`（今日の3つの候補）から返信を外した。`notify.py` は返信を `tasks` から外し、急ぎの判定だけ `allTasks` を見る。
- `desk-morning-mail` を 4:30・15:30 に変えた（スケジュールの揺れは最大約10分）。
- テスト：`lifeos/tests/test_desk_replywin.py`。

## つまずき
- worktree の `desk/data` は試験の残り（`wise/world.json`）で実ディレクトリになっていて、`ln -s` が中に入った。消してから張り直し、`.git/info/exclude` に `/desk/data` を足した。
- プレビューの mobile 表示は viewport の meta がないので 980px で描く。375px の確認は iframe で測った。
