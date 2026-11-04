# 06 初動丸投げ：統括に頼む変更

どれも任意です。入れなくても `/kickoff` と朝の提案は動きます。

## Desk の「Claude に頼む」から `/kickoff` を呼べるようにする

`desk/server.py` の `CLAUDE_CMDS` に足します。

```python
CLAUDE_CMDS = ("/morning-kickoff", "/begin", "/recap", "/rsd", "/learn", "/sync", "/knowledge", "/desk-feature", "/kickoff")
```

`desk/index.html` の `const CC=[` の、`/begin` の行のすぐ下に足します。`id:"kickoff"` は `/morning-kickoff` が使っているので別の名前にします。

```js
  {id:"firststep",e:"🪜",name:"初動を任せる",cmd:"/kickoff",hint:"最初の一歩を Claude がやる",mac:true,ask:"作業名",ph:"例：資料を送る",kw:"しょどう まるなげ kickoff"},
```

`CLAUDE.md` の「決めた8つのコマンド」を「決めた9つのコマンド」にします。
UI の変更なので、`.claude/rules/design-ui.md` の手順で確かめてください。この変更は未確認です。

## 朝のブリーフィング

変更はありません。`lifeos/06-kickoff/brief.py` を `tools.brief_parts()` が拾います。
01番の側で `brief_parts()` を呼んでいれば、提案が混ざります。
