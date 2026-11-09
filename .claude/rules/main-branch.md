# 作ったものは必ず main へ（毎回守る）

Tatsuki の指示（2026-11-09）：Claude が作ったもの・直したものは、どの Claude（クラウドの Claude Code、Mac の Claude Code、スケジュールタスク）でも、最後は必ず **main** に入れる。
Tatsuki のメインのコンピューター（Mac の `~/TatsukiDesk`）は main だけを5分ごとに取り込む（`desk/pull.sh`）。main 以外に置いたものは、Mac の Desk に届かない。

## 手順
1. 作業はブランチでしてよい（クラウドの Claude Code は指定のブランチ）。
2. 確認が済んだら、main への PR を作り、そのままマージまでする。下書きのまま置いておかない。
   - ぶつかったら main を取り込んで解決する。両方の機能を残す。
   - マージできないとき（承認が要る・失敗した）は、何が止めているかを Tatsuki に1行で伝える。
3. Mac で直接作業したときは、コミットして `git push origin main` まで。未コミットのまま残さない。
4. main 以外のブランチ（`desk/page-cards` など）で Desk を動かさない。Mac は常に main にいる。

## してはいけないこと
- main 以外のブランチだけに機能を置いたまま終える。
- Mac を main 以外のブランチに切り替えたままにする（自動の取り込みが止まる）。
- オンライン版（claude.ai の Artifact）だけを直して終える。Desk の正本は main の `desk/index.html` と Mac の `desk/data/`。

## データ
- 個人データ（`desk/data/`）は main に入れない。Mac の中と非公開の保管先だけに置く。
- オンライン版にしかないデータを Mac に移すときは、書き出したファイルを `python3 desk/sync.py merge <ファイル>` で取り込む。

## 終わる前のチェックリスト
- [ ] 変更は main に入った（PR をマージした、または main に push した）
- [ ] Mac の Desk で見られる状態か、Tatsuki に伝えた
