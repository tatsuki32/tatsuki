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
- Mac版は `window.TOOLS` 等（`data/*.js`）と `localhost` の API（`/api/memos|dues|tasks`）を使う。
  Mac側のサーバーとデータは `~/Documents/ClaudeTools` にあり、このリポジトリには無い。
- 削除は `deleted:true` の論理削除。同期は `updatedAt` が新しい方を採用。
- メール送信（Gmail `send_message`）はオンライン版のみ、送信前の確認を必ず残す。

### 公開手順（Claude Code から）

1. `desk/index.html` を編集し、コミットして push する。
2. Artifact ツールで `file_path: desk/index.html`、`url: https://claude.ai/artifact/EkyjR8X6XJdundGEXE9XgP` で publish する。
   - 先に `action: read` で最新版を読み、claude.ai 側で入った変更がないか確認してから上書きする。
   - `capabilities` は渡さない（既存の `db` と Gmail `send_message` を維持するため）。
3. 新しいURLを作らない（同じURLに上書きする）。

### ルール

- 個人データ（メール本文・予定・メモ）をリポジトリに入れない。
- 機能追加は `/desk-feature` の流れ（質問 → 要件定義 → 承認 → 実装）で行う。
- iPhone 幅 375px で横スクロールが出ないこと。
