# 2026-11-03 「＋ 習慣を足す」が動かない

## やったこと
- 原因：名前の入力が `prompt()`。claude.ai の Artifact では prompt()/confirm() が出ず null が返るので、何も起きなかった。
- 画面内の入力ダイアログ `askText()` を足し、習慣の追加・名前変更、新しいノートで使うようにした。
- 習慣の🗑️は `confirm()` をやめ、2回押し（「やめる？」→もう一度）にした。
- 公開版は未マージの `claude/vigilant-babbage-033h8w`（🧭 次は？のタイムライン）を含んでいたので、それをマージしてから直した。

## 次の一手
- Tatsuki がオンライン版で習慣を足せるか確認する。
- このブランチを main にマージする（vigilant-babbage の変更も一緒に入る）。

## 覚えておくこと
- Artifact では prompt()/confirm()/alert() を使わない。#modal のダイアログを使う。
