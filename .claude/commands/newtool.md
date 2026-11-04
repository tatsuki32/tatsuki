---
description: 新しい自作ツールの雛形を作り、Life OS の一覧に載せる。作りかけが3つあるか終了時刻を過ぎていれば止める
allowed-tools: Bash(python3 lifeos/09-toolshop/toolshop.py:*), Bash(test -d:*), Bash(git status:*), Bash(git branch:*), Bash(git switch:*), Bash(git add:*), Bash(git commit:*), Bash(git push:*), Read, Edit
---

# /newtool ツール名 やりたいこと

入力：`$ARGUMENTS`（最初の語がツール名、残りがやりたいこと）

- 空のとき：`python3 lifeos/09-toolshop/toolshop.py list` の結果を見せ、「作るならツール名とやりたいことをどうぞ」とだけ聞く。
- `一覧`・`案` だけのとき：それぞれ `list`・`ideas` の結果を見せて終わる。
- `仕上げ 番号`・`止める 番号`・`再開 番号` のとき：それぞれ `finish 番号`・`set 番号 stopped`・`set 番号 building` を実行し、出た文を伝えて終わる。

## 作るとき

1. ツール名から英小文字とハイフンの短いフォルダ名を決めて、実行する。引数は必ず引用符で囲む。

   ```sh
   python3 lifeos/09-toolshop/toolshop.py new "<ツール名>" "<やりたいこと>" --slug <フォルダ名>
   ```

2. 終了コードが 2 なら、止められたということ。出た文をそのまま伝えて終わる。止まっていても雛形を手で作らない。別の方法で回避しない。
3. 成功したら、できたフォルダと一覧を見せる。中身を実装するかは Tatsuki に聞く。

## クラウドで動いているとき

`test -d ~/TatsukiDesk` が失敗したら、iPhone やオンライン版から頼まれたクラウドの実行。入れ物はあとで消えるので、次のようにする。

- 作れたら、`main` 以外のブランチに、できたフォルダとテストの雛形だけをコミットして push する。ブランチ名を伝え、「main に入ると Mac と一覧に届く」と添える。main へはマージしない。
- 止められたら、案はここに残らない。ツール名とやりたいことを返事にそのまま書き、「Desk のメモに残してください」と伝える。

## 守ること

- 一覧への登録は `tool.json` ができた時点で済んでいる。Desk の Life OS 画面はこれを毎回読む。別の一覧を作らない。
- Desk の「ツール」画面（DB の `tools`）にも載せたいと言われたときだけ、スキル `register-tool-in-toolbox` の手順を使う。
- このリポジトリは公開。実名・社名・案件名・キーを README やコードに書かない。個人データは `paths.data_path` の下に置く。
- 雛形の【未記入】を埋めたら `finish 番号` で稼働中にする。埋まっていないものは稼働中にならない。動かないものの【未記入】を消して通さない。
- やめるときは `set 番号 stopped`。ファイルは消さない。
- Mac では、コミットは Tatsuki が言ったときだけ。
