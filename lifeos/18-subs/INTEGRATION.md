# 18 サブスク自作：統括に頼むこと

## `lifeos/README.md` の「コマンドの持ち主」

`/subs` を新しく足しました（`tool.json` の `commands` に記載）。一覧の行を次のように直してください。

```
`/morning` 01 · `/task` `/next` `/done` `/save` 03 · `/lines` 05 · `/kickoff` 06 · `/newtool` 09 · `/tsuzuki` `/split` 15 · `/where` `/lowenergy` 16 · `/subs` 18
```

理由：請求メールの読み取りはコネクタを使うので、Claude のコマンドとして動かす必要があります。Python からは読めません。

## 09番との合わせ

`/newtool 名前 やりたいこと` の形で1行を出します（名前は空白なし・40字まで。09番の作業中の `toolshop.py` に合わせました）。
読んだのはコミット前の09番です。形が変わったら `core.py` の `newtool_line` を直してください。
名前が日本語だけだと、09番のフォルダ名は `NN-tool` になります。分かりやすくするなら `/newtool` に英語名を渡してください。

## 共有ファイルの変更

ほかにはありません。定期実行も頼みません。`subs.py check` は `/subs` を打ったときに動きます。
