---
description: タスクを Desk に追加する。優先度と案件名は自動で付く（!高 #案件 で指定もできる）
allowed-tools: Bash(python3 lifeos/03-task/cli.py:*)
---

`python3 lifeos/03-task/cli.py add "$ARGUMENTS"` を1回だけ実行し、出力の1行をそのまま返す。ほかの作業は始めない。
内容が空なら実行せず「/task 内容 で追加します」とだけ返す。内容の引用符はシェルで壊れないようにエスケープする。
