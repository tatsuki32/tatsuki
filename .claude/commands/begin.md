---
description: 作業開始。「/begin 〇〇」でタスク登録・main を最新に・GitHub issue を作り、関連ファイルを読み込む（Mac 用）
allowed-tools: Bash(python3 desk/kickoff.py:*), Bash(git status:*), Bash(git switch:*), Bash(git checkout:*), Bash(git branch:*), Bash(git fetch:*), Bash(git pull:*), Bash(gh issue create:*), Bash(gh auth status:*), Read, Glob, Grep
---

# /begin 〇〇（作業開始）

やること：`$ARGUMENTS`（空なら「何を始めますか？一言でどうぞ」とだけ聞く）

環境の準備やファイル探しを Tatsuki にさせない。順に進め、最後に短く報告する。

1. **タスク登録**：`python3 desk/kickoff.py add "<やること>"` で Desk のタスクに「進行中」で登録する（Desk サーバーが止まっていたら、その旨を1行添えて先へ進む）。
2. **main を最新に**：ブランチは作らない。Mac は常に main にいる（ほかのブランチに切り替えると、5分ごとの取り込みが止まる。`.claude/rules/main-branch.md`）。今のブランチが main でなければ、勝手に切り替えず「main にいません（今は …）」と伝える。main なら `git status --short` で未コミットの変更を確かめ、なければ `git pull --ff-only origin main`。変更があれば pull はせず、そのことを1行添える。作業が終わったら main にコミットして `git push origin main` まで。
3. **issue**：`gh auth status` が通るときだけ `gh issue create --title "<やること>" --body "Desk の /begin から作成"`。通らなければ省く（理由を1行）。issue 番号をタスクに伝える必要はない。
4. **読み込み**：やることの言葉で Grep し、関係しそうなファイルを最大5つ Read する。`memory/STATE.md` と `memory/semantic/patterns/` に関係する知識があれば読む。
5. **報告**（6行以内）：

```
🚀 開始：{やること}
🗂️ Desk：進行中に登録 / 🌿 main：最新 / 🐙 issue：#…（できなかったものは理由）
📂 読んだもの：{ファイル名}
⏱ まず5分：{最初の一歩}
```
