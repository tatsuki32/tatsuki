# 2026-11-28 Study Focus のタイマーの円と通知

## ひとこと
- いま：集中の円と通知を足した
- 次：本物の集中で通知を確かめる
- 待ち：なし

## やったこと
- `desk/index.html`：`sfDial`（SVG。r=23 の円を太さ46の線で描き、`stroke-dasharray` で残りの割合）。`sfPanels` が毎秒更新。
- `desk/study_notify.py`：`studylog` の running を5秒ごとに読み、`sfPhase` と同じ計算で切り替わりを通知。ページは "done" を1秒以内に保存するので、前に見ていた回は status done でも「Session done」を出す。
- `desk/server.py`：4747 だけで watch を起動。サーバーの再起動が要る（pull.sh が server.py の変化で再起動する）。
