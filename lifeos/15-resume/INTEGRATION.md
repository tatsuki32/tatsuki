# 15 続き復元：共有ファイルへの変更（入れ済み）

- `.claude/settings.json`：`Stop` と `SessionEnd` に `hook.py` を登録しました。既存の `remind-memory.sh` はそのままです。
- `CLAUDE.md`：コマンド一覧と Life OS の節に足しました。

## 登録のときの注意

- コマンド末尾の `|| true` は外しません。スクリプトがないブランチでは python が終了コード2を返し、Stop フックではセッションが終われなくなります。
- `Stop` も入れる理由：アプリを閉じただけのときは `SessionEnd` が届かない場合があるため。同じ内容は重ねて書きません。
- 動くのは、このリポジトリで開いたセッションだけです。ほかのフォルダの作業も記録したくなったら、本人が `~/.claude/settings.json` に入れます。

## 仕様との違い（判断の記録）

- 作業ログの置き場は仕様の `logs/work/` ではなく、`lifeos/README.md` の表どおり `logs/work.jsonl` にしました。
- `/split` の追加先は仕様の `tasks/` ではなく Desk の `tasks` です。README の「`tasks/MASTER_PLAN.md` は作らない」に合わせました。
- `/recap` は残します。`/tsuzuki` は3行だけ、`/recap` は git の状態まで見る詳しい要約です。`memory/` の読み取りは `desk/kickoff.py` の `memory_doc()` をそのまま使い、作り直していません。
