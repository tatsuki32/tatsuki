---
description: 並列ライン。複数の Claude Code セッションの一覧。始める・止める・終了時刻・週の稼働時間
allowed-tools: Bash(python3 lifeos/05-parallel/lines.py:*)
---

# /lines（並列ライン）

引数：`$ARGUMENTS`

1. 引数がなければ `python3 lifeos/05-parallel/lines.py` を実行し、出力をそのまま見せる。要約しない。
2. 引数があれば、次の形に直して実行し、出力をそのまま見せる。

| やりたいこと | コマンド |
| --- | --- |
| 始める | `lines.py start 名前 --until 18:30 --what "何をするか"`（`--branch` `--base` は任意） |
| どこまで進んだかを書く | `lines.py note 名前 "テストが通った"` |
| 止める | `lines.py pause 名前 "理由"` |
| 再開する | `lines.py resume 名前 --until 20:00` |
| 終了時刻を延ばす | `lines.py extend 名前 30m` |
| 終える | `lines.py end 名前`（止めた時刻を直すなら `--at 18:30`） |
| 週の稼働時間 | `lines.py week` |
| すべての worktree | `lines.py all` |

## 守ること

- 終了時刻を本人に聞かずに決めない。`start` と `resume` で分からなければ聞く。
- `止めました：` で断られたら、そのまま伝える。上限を変えて通そうとしない。上限の変更（`lines.py limit N`）は本人が頼んだときだけ。
- `lines.py remove 名前` は作業フォルダを消す。まず `--yes` なしで実行して内容を見せ、本人が承認してから `--yes` を付ける。
