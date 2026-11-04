# 05 並列ライン：共有ファイルへの変更案

どれも無くても `/lines` は動きます。1から順に価値が高いものです。まだどれも適用していません。動作も未確認です。

## 1. Desk の Life OS 画面に今のラインを出す

`tool.json` は git に入るので、ライン名を書けません。そこで `window.LINES` を別に渡します。`lifeos_tools()` に混ぜると、毎時同期でオンラインにも届くので混ぜません。

`desk/server.py` の `lifeos_tools()` の下に足します。

```python
def lines_now():
    """Life OS 05: the open lines, for the Mac page only. The hourly sync does not carry this."""
    try:
        r = subprocess.run([sys.executable, os.path.join(REPO, "lifeos", "05-parallel", "lines.py"), "json"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
        return json.loads(r.stdout.decode("utf-8"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
```

`page()` の `parts.append(f"window.LIFEOS={los};")` の次の行に足します。

```python
    parts.append("window.LINES=" + json.dumps(lines_now(), ensure_ascii=False).replace("</", "<\\/") + ";")
```

`desk/index.html` の `losTool` で、`${t.updated?` で始まる行の前に足します。

```js
      ${slug==="05-parallel"&&window.LINES?`<section><h3>今のライン ${esc(window.LINES.open)}/${esc(window.LINES.limit)}</h3>${window.LINES.banner.map(x=>`<p><b>${esc(x)}</b></p>`).join("")}<ul>${window.LINES.lines.map(l=>`<li><b>${esc(l.name)}</b>：${esc(l.state)}<br>${esc(l.what)}<br>${esc(l.where)}</li>`).join("")}</ul></section>`:""}
```

UI の変更なので、適用するときは `.claude/rules/design-ui.md` の手順に従ってください。

## 2. Claude Code のセッション内にも知らせる

`.claude/settings.json` の `hooks` に足します。終了時刻を過ぎている間だけ、依頼のたびにセッションへ知らせが届きます。過ぎていなければ何も出ません。

```json
"UserPromptSubmit": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python3 \"$CLAUDE_PROJECT_DIR/lifeos/05-parallel/lines.py\" banner 2>/dev/null || true"
      }
    ]
  }
]
```

## 3. Desk の「Claude に頼む」に /lines を足す

`desk/server.py` の `CLAUDE_CMDS` に `"/lines"` を足します。`desk/index.html` の `/rsd` の行の並びに、次を足します。

```js
  {id:"lines",e:"🧵",name:"並列ライン",cmd:"/lines",hint:"動いているセッションの一覧",kw:"へいれつ らいん worktree"},
```

他の項目の形に合わせて、`ask` などの要否は適用時に確かめてください。

## 4. 定期実行で通知を確実にする（任意）

見張りは `/lines` を打ったプロセスから始まります。Claude Code のサンドボックス内で始まると、Mac の通知と Telegram が通らない場合があります。

launchd か 10番の仕組みで、次を5分ごとに動かすと、その影響を受けません。

```sh
python3 ~/TatsukiDesk/lifeos/05-parallel/lines.py check
```
