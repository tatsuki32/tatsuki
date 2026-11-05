# 2026-11-05 タスクを時期で畳み、並び替えを選べるようにした

## ひとこと
- いま：タスクを時期で分けられる
- 次：朝の見積もりの実走を見る
- 待ち：案を見て振り分ける

## やったこと
- `desk/index.html`：列の中を時期のトグルで分けた（`tPeriods`・`taskGroups`）。カードと詳細に「いつやる」、詳細に所要時間、列の上に並び替え。タスク画面の期限表示から赤を外した。
- `desk/task_estimate.py`：定期タスク `desk-task-estimate` 用。`pending` と `apply`。本人の `planDate`・`estMin` は変えず、`updatedAt` も動かさない。
- テスト：`lifeos/tests/test_desk_task_estimate.py`。画面は試しデータの別コピー（別ポート）で 1200px と 375px を確認。

## 決めたこと
- 時期は名前でなく期間の最後の日で持つ。放っておくと今月→今週→今日へ自動で移る。
- 完了の列の「期限順」は、終わった順のまま。

## 気づいたこと
- 他のセッションと同じ `desk/index.html` を同時に触ると、向こうのコミットに混ざる（今回 `fa551f3` に入った）。
- `desk/index.html` は viewport の meta がないので、375px の確認は同じ生成元の iframe で行う。

## 残り
- `lifeos/tests` の2件の失敗（test_14 の bot、test_11 の定期タスク説明）は今回の変更とは別。
