---
description: 文脈の要約。現状・直前の決定・次の一手を10秒で読める形にする（「保存」で今の作業をセッション記録に残す）
allowed-tools: Read, Glob, Grep, Bash(git status:*), Bash(git log:*), Bash(git diff:*), Bash(git branch:*)
---

# /recap（文脈要約。Claude Code 標準の /resume と区別するためこの名前）

引数：`$ARGUMENTS`（空 = 読む / `保存` = 今の作業を記録してから要約）

## 読む（引数なし）
1. `memory/STATE.md` と、`memory/episodic/sessions/` の新しい3件を読む。
2. `git branch --show-current`、`git status --short`、`git log --oneline -5` で、記録のあとに進んだことがないか確かめる。
3. 次の形で出す。**8行以内**、それ以上は書かない：

```
📍 現状：{いま何をしていて、どこまでできているか 1〜2行}
✅ 直前の決定：{いちばん新しい決定 1〜2個}
⏸ 止まっている所：{ファイル名 / 何待ちか}
👉 次の一手：{最初にやる1つ。動詞で始める}
根拠：{読んだファイル名}
```

- 記録と git の状態が食い違っていたら（未コミットの変更がある、記録にないコミットがある）、「記録にない変更：…」を1行足す。
- 推測で埋めない。分からないところは「記録なし」と書く。

## 保存（引数 `保存`）
このセッションでやったことを `memory/episodic/sessions/YYYY-MM-DD-話題.md`（今日のファイルがあれば追記）に `memory/README.md` の雛形で書き、`memory/STATE.md` の「進行中の作業」を更新してから、上の要約を出す。
公開リポジトリのルール：メール本文・予定・メモの本文・健康のこと・実名・URL の秘密部分は書かない。
