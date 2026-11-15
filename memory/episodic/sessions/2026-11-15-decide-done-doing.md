# 2026-11-15 やらないカードに Done と In progress

## ひとこと
- いま：やらないカードに2つのボタン
- 次：使ってみて直す
- 待ち：なし

## やったこと
- `openDeck` に `o.extra`（`{id,label,key,act}`）を足した。← → 以外の選択肢をボタンとキーで出せる。
- `openDecide` に Done（D。`status:"done"` と `taskWin` で wins）と In progress（P。`status:"doing"`、`skip.kept` も付けて30日出さない）。
- Z で戻すと Done の wins（`task-<id>`）も消す。区切りの文に「◯ done, ◯ started」。
- テストサーバーで保存を差し替えて確かめた（本物のデータは書き換えていない）。375px で横スクロールなし。
