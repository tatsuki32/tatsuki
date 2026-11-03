# CLAUDE.md

## Tatsuki's Desk

Tatsuki の個人ダッシュボード。**正本は `desk/index.html`**（このリポジトリ）。
claude.ai のチャットで直接編集しない。変更は必ずここで行い、ここから公開する。

- 公開先（オンライン版）: https://claude.ai/artifact/EkyjR8X6XJdundGEXE9XgP
- 要件定義 v2: https://claude.ai/artifact/2TuwLFWA831xA7mKDd2eLP

### 構成

- 単一HTML（外部ライブラリなし、Google Fonts のみ）。黒基調・金色アクセント。
- ビュー: Home / Tasks / Calendar / World Desk / Morning Mail / Inbox / Claude Log
- データはページに埋め込まない。オンライン版は Artifact DB（`window.claude` の `db`）から読む:
  `tools`, `memos`, `tasks`, `dues`, `calendar`(doc `upcoming`), `gtasks`(doc `current`),
  `reports`, `editions`, `days`
- Mac版も同じ `desk/index.html` を使う。`desk/server.py`（Python標準ライブラリのみ）が
  `http://localhost:4747` で配信し、`desk/data/*.js`（`window.TOOLS` 等）をページに差し込む。
  API: `GET /api/memos|dues|tasks`、`POST /api/memos`、`PATCH /api/memos/<id>`、`PUT /api/dues/<id>`、`PUT /api/tasks/<id>`。
  データは `desk/data/*.json`（`.gitignore` 済み。個人データは絶対にコミットしない）。
- Mac の置き場所は `~/TatsukiDesk`（Documents などの保護フォルダは自動起動から読めないので不可）。
- Mac の導入: `bash desk/setup-mac.sh`（サーバー常駐＋5分ごとの `git pull origin main`）。
  旧版 `~/Documents/ClaudeTools` は使わない（初回だけ memos/dues/tasks を取り込む）。
- 同期: `desk/sync/HOURLY_SYNC.md` を Mac の Claude スケジュールタスクで毎時実行。
  memos/dues/tasks は双方向、それ以外はオンライン → Mac。`desk/index.html` が変わっていればオンライン版にも公開する。
- 削除は `deleted:true` の論理削除。同期は `updatedAt` が新しい方を採用。
- メール送信（Gmail `send_message`）はオンライン版のみ、送信前の確認を必ず残す。

### 公開手順（Claude Code から）

1. `desk/index.html` を編集し、コミットして push する。Mac は `main` を自動で pull するので、
   クラウドの `claude/...` ブランチの変更は main にマージされてから Mac に届く。
2. Artifact ツールで `file_path: desk/index.html`、`url: https://claude.ai/artifact/EkyjR8X6XJdundGEXE9XgP` で publish する。
   - 先に `action: read` で最新版を読み、claude.ai 側で入った変更がないか確認してから上書きする。
   - `capabilities` は渡さない（既存の `db` と Gmail `send_message` を維持するため）。
3. 新しいURLを作らない（同じURLに上書きする）。

### ルール

- 個人データ（メール本文・予定・メモ）をリポジトリに入れない。
- 機能追加は `/desk-feature` の流れ（質問 → 要件定義 → 承認 → 実装）で行う。
- iPhone 幅 375px で横スクロールが出ないこと。
- 画面に出る文字はすべて日本語（固有名詞を除く）。データに保存する判定用の値（`priority` の `Action needed`、`region`、ツールの `group`、タスクの `status` など）は英語のままにして、表示だけ日本語にする（`PRIJA` / `REGJA` / `GROUPS`）。
  スケジュールタスク（World Desk morning brief / Morning Mail check）も日本語で書く。メールは `subject_ja`・`draft.body_ja` を足し、Gmail の下書き本体は相手の言語のまま。
