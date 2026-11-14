# 2026-11-14 ルーティンの夜の静かな時間

## ひとこと
- いま：Mac の朝4時のアラームを無音にした
- 次：明日の朝、鳴らないか見る
- 待ち：なし

## やったこと
- `desk/routine_alarm.py` に静かな時間 `QUIET`（ホーチミン 22:00〜7:00）。この間は音も読み上げもなし、Routine の開始画面 `#start/<when>` を開くだけ。
- `routine/public/index.html` の「開いている間のアラーム」も、localhost（Mac）のときだけ同じ時間は鳴らさない（`macQuiet`）。iPhone は今まで通り。
- 設定（`lists` の `at`・`alarm`）は Mac のデータなので触っていない。完全に止めたいときは Routine の ✏️ Edit で Start alarm を Off。
