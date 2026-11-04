# 09 ツール工房

## 目的

新しい自作ツールを、同じ型で素早く始めます。作りかけが増えすぎないように止める役目も持ちます。

## 使い方

Claude Code で次のように打ちます。

```
/newtool ツール名 やりたいこと
```

`lifeos/NN-名前/` に README（目的・使い方・止め方）、`core.py`、`tool.json` ができます。テストの雛形は `lifeos/tests/` に入ります。
`tool.json` ができた時点で、Desk の Life OS 画面に載ります。

コマンドでも動かせます。

```sh
python3 lifeos/09-toolshop/toolshop.py list               # 名前と状態の一覧
python3 lifeos/09-toolshop/toolshop.py finish 19          # 仕上げて稼働中にする
python3 lifeos/09-toolshop/toolshop.py set 19 stopped     # 止める（building / working / stopped）
python3 lifeos/09-toolshop/toolshop.py ideas              # 後回しにした案を見る
```

## 止まるとき

| 場面 | どうなるか |
| --- | --- |
| 作りかけ（`building`）が3つある | 作りません。先に1つ仕上げるよう促します |
| 終了時刻のあと（23:00〜05:00） | 作りません |
| 同じ名前がある | 作りません |

止まった案は消えません。`ideas` で見られます。
上限の3は変えられません。時刻は `desk/data/lifeos/tools/09-toolshop/config.json` で変えられます。

```json
{"endTime": "23:00", "resumeTime": "05:00"}
```

`planned` と `blocked` は数えません。`blocked` は外の都合で待っているものだからです。
`finish` は、雛形の「未記入」の印がファイルに残っていると断ります。

## 止め方

```sh
python3 lifeos/09-toolshop/toolshop.py set 09 stopped
```

`new` だけが止まります。一覧と状態の変更は動きます。再開は `set 09 working` です。
どのツールも、止めてもファイルは消えません。削除の機能は置いていません。

## 知っておくこと

- 置き場は `tools/` ではなく `lifeos/NN-名前/` です。Desk の一覧が `lifeos/*/tool.json` を読むからです。リポジトリの `tools/` は文章チェックの道具の置き場です。
- 作ったツールは git に入ります。コミットは自動ではしません。公開リポジトリなので、実名や案件名は書きません。
- Desk の「ツール」画面（DB の `tools`）には自動で載せません。載せたいときはスキル `register-tool-in-toolbox` の手順を使います。
- Desk の画面では、状態は「作っている・動いている・止めた」と出ます。一覧の「作成中・稼働中・停止」と同じ意味です。
