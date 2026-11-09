# 2026-11-09 Mac の Desk にプロジェクトを届ける

## ひとこと
- いま：Mac 用の版にプロジェクトを入れた
- 次：Mac で取り込みのコマンドを1回打つ
- 待ち：Mac での取り込み（本人の操作）

## やったこと
- main にマージしたプロジェクト機能が Mac に出ない原因を調べた。Mac は main ではなく `desk/page-cards` で動いていて、自動 pull（main のときだけ動く）が止まっていた。
- `desk/page-cards` に main を取り込んだ（プロジェクト＋持ち物）。ぶつかり：`index.html` 14か所、`server.py`・`sync.py`・`CLAUDE.md`・`STATE.md` 各2か所。Mac 側の機能はすべて残し、プロジェクトの部品を足した。`sync/HOURLY_SYNC.md` は Mac 側で消してあるので消したまま。
- 不具合を直した：タスクの画面で Mac 側の `tEst`（かかる時間・分）・`tStart`（始めるボタン）とプロジェクトの欄の id がぶつかり、所要時間が保存で消えていた → `tPEst`・`tPStart` に変えた。
- Mac と同じ形（`server.py`＋`sync.py merge` で初期データ）で Playwright 確認：左メニュー、箱 6・49 件、チェック、編集の保存（所要時間が残る）、各ビュー、375px で横スクロールなし。

## 決めたこと
- 結果は自分のブランチ `claude/ecstatic-turing-hp7fh9` に push（`desk/page-cards` へは直接 push しない）。Mac で `git pull origin claude/ecstatic-turing-hp7fh9` すれば早送りで入る。
- 初期データは個人情報入りなのでリポジトリに入れない。Mac で `python3 desk/sync.py merge <ファイル>` で取り込む。

## 中断した場所
- Mac に未コミットの変更（「身体」「お知らせ」など）があるので、pull の前にそれをコミットする必要がある。

## 次の一手
- Mac の Claude Code で、未コミットの変更をコミット → pull → 初期データの取り込み。
