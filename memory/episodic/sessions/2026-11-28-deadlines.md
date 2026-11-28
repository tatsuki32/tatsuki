# 2026-11-28 メールの締め切りをカレンダーへ（📅 Deadlines）

## ひとこと
- いま：締め切りを自動でカレンダーへ
- 次：Google の許可を1回押す
- 待ち：本人の Allow

## やったこと
- `desk/deadlines.py`：見つけた締め切りの重複・移動・消したものの判定、Google カレンダー「Deadlines」への書き込み、本人の受信トレイへの1通（messages.import）。許可は calendar.app.created だけ。
- `desk/unimail.py recent`：大学の受信トレイ36時間（自動の通知も）を標準出力に。
- Desk の画面 `#deadlines`（部品は `mdl`。`dl`・`S.dl` はメールの期日の欄が使っていてぶつかった）。
- 定期タスク `desk-morning-mail` に Part A3。
- 試験 `desk/tests/test_deadlines.py`（13本）、試験サーバー `desk-deadlines-test`（4773、偽の Google）。

## 気づき
- 題の似ている判定は文字の2つ組。「Report 2」と「Report 3」がまとまったので、数字が違えば別物にした。
- 画面が古いままに見えたのはブラウザの読み込み直しが要っただけ。

## 次の一手
- 本人が `python3 desk/deadlines.py auth`（または画面の Connect Google Calendar）で Allow → 次の 4:30／15:30 の実行で本物の1件を確かめる。
