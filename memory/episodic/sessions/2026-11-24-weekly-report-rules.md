# 2026-11-24 週間レポートを受けた決まり

## ひとこと
- いま：寝る時刻と決まりを入れた
- 次：夜のブロックを本人が直す
- 待ち：4通の送信の確認

## やったこと
- Bedtime を 21:30〜4:30 に（`desk/bedtime.py`）。前に保存した 22:00 は1回だけ 21:30 に直す（`v: 2`）。
- `desk/once.py`：Mac だけにあるデータを一度だけ直す仕組み。サーバーが 4747 で起動したときに1回。最初の1件は「14日より古い催促のタスクを全部 Not doing」。
- Claude の決まりを3つ：`tool-urge.md`（仕事に関係ない道具は2回断ってメモ、3回目は「今のタスクの後」）、`night.md`（21:30〜4:30 は寝てもらう）、`shopping.md`（買う前に今月の入金を聞く）。夜の時刻は Mac の UserPromptSubmit フック `night.sh` が知らせる。
- 置いておく道具のメモ：`memory/semantic/parked-tools.md`。

## 残り
- FocusBlock の夜のスケジュール（21:30〜4:30、Instagram・Brave・Yandex・LINE・WhatsApp）は、1日中のスケジュールが動いているため設定の変更にパスワードが要る。本人が FocusBlock の設定画面で直す。

## 続き（同じ日）
- `once.py` に2つ目の1回だけの処理：今日のタスク「Reply to Takahashi (Helpfeel)」を足す。新しい処理だけでは pull.sh がサーバーを再起動しないので、`server.py` のコメントも変えた。
