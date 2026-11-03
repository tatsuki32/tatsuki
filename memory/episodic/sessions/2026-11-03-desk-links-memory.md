# 2026-11-03 Desk に新機能をまとめて追加

## ひとこと
- いま：Desk と Claude の新機能を全部入れ終わった
- 次：Mac で実際に使ってみて、使いにくい所を直す
- 待ち：リポジトリを非公開にする（GitHub の設定）
- 待ち：VINEPRO Board の説明と、Mac で使う番号を教えてもらう

## やったこと
- Home のツールタイルに ✏️「URLを変更」を追加（`urlEditable:true` のツールだけ、オンライン版だけ）。Mac で動く自作ツールを「VINEPRO Board」として `tools` に登録。
- ニュース・マイナー国で文字を選ぶと「🔗 リンクにする」。記事の中でリンクになり、裏で出典つきのノートができる。
- リンクの色分け（青＝つながり、赤＝孤立、外部＝金）と、ノート下の「🕸️ つながり」（2-hop）。
- 作りたい機能リスト11件を Desk のタスクに登録（ID `tfeat01`〜`tfeat11`、メモに [作りたい機能リスト]）。
- memory/ と CLAUDE.md の「記憶の使い方」、Stop フック（記録の書き忘れ防止）を追加。
- 残りの作りたい機能を一気に実装：/recap・/rsd・/begin・/sync・/learn・/knowledge、.claude/rules/（文体・プライバシー・ペルソナ）、.githooks/pre-commit（漏洩防止）、tools/validate-writing.mjs（npm run validate-writing）、Desk の「☀️ 今日の3つ」と「🌱 習慣」（新コレクション habits、双方向同期）。
- Desk に「🧭 次は？」ボタン（右上・G キー）：何していたか（memory/ の止まった所・次の一手、進行中、最近さわったもの）と、次はこれ（理由・次の予定までの時間・始める/完了/別の/🤖 最初の一歩）。memory/ は Mac では server.py が直接、オンラインは DB の memory/current（毎時同期）。
- /morning-kickoff：`desk/kickoff.py` が Mac の `desk/data/` と memory/ から候補を JSON で出し、コマンドが1つ選んで「まず5分」を提案。「始める」で `PUT /api/tasks/<id>`。

## 決めたこと
- 色のルールは Scrapbox 式。出典の行だけのノートは「中身なし」と数える（リンクしたばかりの言葉は赤から始まる）。
- 保存の形は変えない（`notes` の title/body から毎回数える）→ 古い Mac 版と混ざっても壊れない。
- 外付けメモリはこのリポジトリに置く。公開リポジトリなので技術的な中身だけ書く。
- メール由来タスクの ID は index.html の dueDocId と同じ計算（kickoff.py の due_doc_id、絵文字込みで一致を確認）。
- リポジトリは非公開にする（Tatsuki が GitHub の設定で切り替える）。

## 中断した場所
- tatsuki32/tatsuki#9 を main にマージ済み。Tatsuki の作業待ち：リポジトリを非公開に切り替え（その後 Mac で gh auth login / gh auth setup-git）、setup-mac.sh の再実行（フック有効化）。
- VINEPRO Board の localhost のポート番号と説明文は未定（Tatsuki に確認中）。

## 次の一手
- Mac で /morning-kickoff と pre-commit フックを実際に動かして直す。
- ペルソナ（.claude/rules/persona.md）の中身を Tatsuki と決める。
