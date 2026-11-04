# 10 決断ゼロ起動：共有ファイルへの変更

## 1. `.claude/settings.json` のフック（導入済み）

`hooks.SessionStart` の配列に、次の1件を足してあります。別のブランチとぶつかったら、両方の項目を残します。

```json
{
  "matcher": "startup|clear",
  "hooks": [
    {
      "type": "command",
      "command": "python3 \"$CLAUDE_PROJECT_DIR/lifeos/10-autostart/autostart.py\" hook",
      "timeout": 20
    }
  ]
}
```

- 新規開始と `/clear` のときだけ動きます。`resume` と `compact` は、作業の途中なので出しません。
- 15番もフックを足すはずです。別の項目として並べれば衝突しません。

## 2. 入れた直後に起きること

- フックが入ったフォルダの最初のセッションで、14日以上さわっていないタスクがまとめて「アーカイブ」に移ります。
- その前に本人が `defer --dry` で一覧を見られるよう、手順を `tool.json` の `howToCheck` に書きました。
- 全部戻すのは `python3 lifeos/10-autostart/autostart.py restore --all` です。

## 3. このフックが届かない場所（本人の判断）

このフックは `~/TatsukiDesk` と、その worktree のセッションでだけ動きます。
ほかのフォルダで Claude Code を始めても、最初の1件は出ません。
どこで始めても出したいなら、`~/.claude/settings.json` に絶対パスで入れます。全体設定なので、本人が決めてください。

毎時同期などの自動セッションでも動きます。別の依頼があるときは1行添えるだけ、と文面で指示しています。

## 4. 01番との関係

最初の1件の選び方は `/morning-kickoff` の順番と同じです（`core.py` の `pick_first`）。
01番の `pick` も同じ順番です。単体で動かすため共有していません。順番を変えるときは両方を直します。
