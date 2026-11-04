---
description: タスクを完了にする。何も付けなければ /next で出した1件
allowed-tools: Bash(python3 lifeos/03-task/cli.py:*)
---

`python3 lifeos/03-task/cli.py done "$ARGUMENTS"` を1回だけ実行し、出力をそのまま返す。
候補が並んだときは推測で選ばず、本人が選ぶのを待つ。
