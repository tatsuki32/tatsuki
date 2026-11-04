# Tatsuki's Desk 毎時同期（Mac の Claude スケジュールタスク用）

この手順は、Mac の Claude デスクトップアプリのスケジュールタスクで、このリポジトリのフォルダ（`~/TatsukiDesk`）を開いて毎時実行する。
目的：Mac 版とオンライン版の Desk を、**データもコードも**同じ状態に保つ。

- オンライン版: https://claude.ai/artifact/EkyjR8X6XJdundGEXE9XgP
- `DESK` = このファイルの1つ上のフォルダ（`desk/`）

## 1. コードを最新にする

```bash
git -C "$DESK/.." pull --ff-only origin main
```

失敗したら（ローカルに未コミットの変更があるなど）、内容を報告して手順 2 に進む。勝手にリセットしない。

## 2. データを同期する（memos / dues / tasks / prompts / notes / habits / wins は双方向、lifeos は Mac → オンライン、それ以外はオンライン → Mac）

1. ArtifactData でオンライン版の次のコレクションを全件読む（`list`、ページがあれば最後まで）:
   `memos`, `dues`, `tasks`, `prompts`, `notes`, `habits`, `wins`, `lifeos`, `followups`, `tools`, `calendar`, `gtasks`, `reports`, `watch`, `editions`, `days`
   - `editions` と `days` は新しい順に 60 件 / 30 件あれば十分
2. 読めたものだけを、スクラッチの一時ファイル `remote.json` に次の形で書く
   （読めなかったコレクションはキーごと省く。空配列にしない。空にすると Mac 側だけのデータが消えたように見えるため）:
   `{"memos": [{"id": "<doc id>", ...そのドキュメントのフィールド}], "dues": [...], ...}`
3. 実行:
   ```bash
   python3 "$DESK/sync.py" merge remote.json
   ```
   Mac の `data/*.json` と `data/*.js` が更新され、`{"write": {"memos": [...], ...}}` が出力される。
4. `write` に入っているドキュメントを、ArtifactData の `batch` でオンライン版に `set` する
   （ドキュメント id は各要素の `id`。`id` フィールド自体は書かない）。`write` が空なら何もしない。
5. `remote.json` を消す（メール本文などを残さない）。

ルール：同じ id なら `updatedAt` が新しい方を採用。削除は `deleted:true` のレコードとして同期し、ドキュメント自体は消さない。

`followups`（Desk の「返信待ち」画面）は Mac が正本で、件名と相手のアドレスだけが載る。iPhone の答えは `answer` 欄で届き、`merge` が先に Mac へ反映してから、`write` で最新の状態を返す。`write` の内容をそのまま `set` する（`answer` は消えてよい）。メールの送信はしない。読めなかったときはキーごと省く。

`lifeos`（Desk の「Life OS」画面。中身は `lifeos/*/tool.json`）は一方向：リポジトリが正本で、Mac に書き戻さない。違っているものだけが `write` に出るので、上の手順 4 で一緒に `set` する。オンラインにしかない id は `deleted:true` で出る。読めなかったときはキーごと省く（何も書かれない）。

## 2b. 作業の記録（memory/）をオンライン版に載せる（Desk の「🧭 次は？」用）

```bash
python3 "$DESK/kickoff.py" memory-doc > memory-doc.json
```

ArtifactData で `memory` コレクションの doc `current` に、`file_path: memory-doc.json` で `set` する（既存なら先に `get` して `if_version` を付ける）。終わったら `memory-doc.json` を消す。
Mac 版の Desk はサーバーが毎回 memory/ を直接読むので、この手順は要らない。

## 3. コードをオンライン版に反映する

```bash
python3 "$DESK/sync.py" code-status
```

`"changed": true` のときだけ：

1. Artifact ツールで `action: read`、`url` = オンライン版。最新版が前回 Claude Code から公開した内容以外に変わっていないか確認する（claude.ai 上で直接編集された形跡があれば公開せず報告）。
2. Artifact ツールで publish：`file_path` = `desk/index.html`、`url` = オンライン版。`capabilities` は渡さない（保存済みの宣言がそのまま引き継がれる）。
3. 成功したら `python3 "$DESK/sync.py" mark-published`

## 4. 報告

1〜3 の結果を短くまとめる（同期した件数、公開したかどうか、エラー）。何も変わらなければ「変更なし」だけでよい。
