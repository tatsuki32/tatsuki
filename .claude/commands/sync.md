---
description: 方針が変わったときに状態ファイル（memory/STATE.md・CLAUDE.md・.claude/rules/）を更新する
allowed-tools: Read, Edit, Glob, Grep, Bash(git status:*), Bash(git diff:*)
---

# /sync（方針の更新）

変わった方針：`$ARGUMENTS`（空なら「何が変わりましたか？」とだけ聞く）

1. 変わった方針を1〜2行で言い直し、どのファイルに書くかを決める：
   - 今の作業の状態・決定 → `memory/STATE.md`（「決定済みの事項」。古い決定と矛盾するなら古い方を消す。40行まで）
   - プロジェクトの構成・手順 → `CLAUDE.md`
   - 毎回守るルール（文体・プライバシー・発信） → `.claude/rules/` の該当ファイル
2. 変更は最小限。関係ない行は触らない。
3. 変えた場所を「ファイル名：前 → 後」の形で3行以内で報告する。コミットは Tatsuki が頼んだときだけ。
公開リポジトリのルール：個人データ・健康のこと・URL の秘密部分は書かない。
