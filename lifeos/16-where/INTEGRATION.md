# 16 案件復元：統括への依頼

なくても `/where` と `/lowenergy` は Claude Code で動きます。Desk のボタンから始めたいときだけ入れてください。

## 1. `desk/server.py`：許可するコマンドに足す

```python
CLAUDE_CMDS = ("/morning-kickoff", "/begin", "/recap", "/rsd", "/learn", "/sync", "/knowledge", "/desk-feature", "/where", "/lowenergy")
```

## 2. `desk/index.html`：`const CC=[` の一覧の `recap` の次に足す

```js
  {id:"where",e:"🧩",name:"案件を思い出す",cmd:"/where",hint:"どこまで・残り・次の一手を5行で",mac:true,ask:"案件名（空なら候補を出す）",ph:"例：資料の直し",kw:"あんけん where"},
  {id:"lowenergy",e:"🌙",name:"元気がない日",cmd:"/lowenergy",hint:"下書きに OK か直すで返すだけ",mac:true,ask:"案件名（空なら Claude が選ぶ）",ph:"空のままで大丈夫",kw:"げんきがない lowenergy"},
```

`mac:true` にするのは、どちらも Mac の `where.py` を動かすためです。

## 3. `CLAUDE.md`：「決めた8つのコマンド」を「決めた10のコマンド」にする

## 確かめていないこと

この3つは適用していないので、ボタンからの起動は未確認です。適用後に Desk のボタンから1回試してください。
