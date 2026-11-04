# 15 続き復元：統括に頼む変更

## 1. `.claude/settings.json` にフックを足す（必須）

`hooks` の中の `Stop` の配列に、下の `Stop` の1件を足します。`SessionEnd` は新しい項目です。既存の `remind-memory.sh` はそのまま残します。

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/lifeos/15-resume/hook.py\" 2>/dev/null || true"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/lifeos/15-resume/hook.py\" 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

- `|| true` は外しません。スクリプトがないブランチでは python が終了コード2を返し、Stop フックではセッションが終われなくなります。
- `Stop` も入れる理由：アプリを閉じただけのときは `SessionEnd` が届かない場合があるため。同じ内容は重ねて書きません。
- このフックが動くのは、このリポジトリで開いたセッションだけです。ほかのフォルダの作業も記録したくなったら、本人が `~/.claude/settings.json` に入れます。

## 2. 共有の説明の小さな修正（任意）

`CLAUDE.md` の「Claude Code のコマンド」の行に `/tsuzuki`（3行で続きを出す）と `/split`（15分以内に分ける）を足すと、一覧が実物と合います。

## 3. 仕様との違い（判断の記録）

- 作業ログの置き場は仕様の `logs/work/` ではなく、`lifeos/README.md` の表どおり `logs/work.jsonl` にしました。
- `/split` の追加先は仕様の `tasks/` ではなく Desk の `tasks` です。README の「`tasks/MASTER_PLAN.md` は作らない」に合わせました。
- `/recap` は残します。`/tsuzuki` は3行だけ、`/recap` は git の状態まで見る詳しい要約です。`memory/` の読み取りは `desk/kickoff.py` の `memory_doc()` をそのまま使い、作り直していません。
