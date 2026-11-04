# 16 案件復元：共有ファイルの変更

本人の依頼で、Desk のボタンを入れました。共有ファイルを3つ変えています。

| ファイル | 変更 |
| --- | --- |
| `desk/server.py` | `CLAUDE_CMDS` に `/where` と `/lowenergy` を足した |
| `desk/index.html` | `const CC=[` の `recap` の次に2行足した |
| `CLAUDE.md` | コマンドの数と一覧を直した |

`/lowenergy` には入力欄を付けていません。元気がない日に入力を求めないためです。案件を指定したいときは、Claude Code で `/lowenergy 案件名` と打ちます。

## オンライン版

オンライン版では、押すとコピーして Claude Code を開きます。`where.py` は Mac のリポジトリで動かすものなので、どちらのボタンにも「Mac」の印が出ます。
