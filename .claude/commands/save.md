---
description: 今の作業状態（やったこと・次の一手）を保存する
allowed-tools: Bash(python3 lifeos/03-task/cli.py:*)
---

このセッションの内容から、やったことを1文、次の一手を動詞で始まる1文にする。`$ARGUMENTS` があれば優先して使う。
`python3 lifeos/03-task/cli.py save "<やったこと>" --next "<次の一手>"` を1回だけ実行し、出力をそのまま返す。
本文は個人データ置き場に入り、git には入らない。コミットはしない。セッション記録まで残すときは /recap 保存 を使う。
