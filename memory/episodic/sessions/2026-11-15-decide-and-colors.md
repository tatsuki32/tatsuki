# 2026-11-15 決める入口を目立たせ、地の色を明るく、ジャンルに色とスタンプ

## ひとこと
- いま：決める入口を Home と Tasks に置いた
- 次：本人が使ってみて色を直す
- 待ち：月曜の見直しの実行

## やったこと
- `decideBar`：Decide what not to do・Answer open questions・Merge similar tasks・Review task types を件数つきの大きなボタンで、Home の今日の3つの真下と Tasks の上に出す。押すとすぐカード。前の小さなリンクは外した（`/` 一覧は残る）。
- 地の色：`:root` の値だけを変えた（`--bg:#1c1d21`、`--surface:#26282d`、`--surface-2:#30323a`、`--muted:#a0a1aa` など）。muted の文字でも一番明るいカードの上で 5.0:1。
- ジャンルの `color`（10色）と `emoji`。タスクのカード（`gchip`）・絞り込み・Genres・Time に出す。Genres の Edit で変えられる。今の18個に色を付け、スタンプのないもの3つに絵文字を付けた（`genre-apply` 経由なので変更メモに残り、Undo できる）。
- 週の見直し：`genre-apply` に `retire` と `add`、変更は `genrelog`。種類は `type-review`／`type-propose` で案だけ（`typeprops`）、カード `openTypes`。退けた種類は `tasktypes` の `retired:true`（タスクの `cat` はそのまま）。
- 前後のスクリーンショット（1200px）は共有メモのフォルダに置いた（タスクの題が写るのでリポジトリには入れない）。
