# CLAUDE.md

## Tatsuki's Desk

Tatsuki の個人ダッシュボード。**正本は `desk/index.html`**（このリポジトリ）。
claude.ai のチャットで直接編集しない。変更は必ずここで行い、ここから公開する。

- 公開先（オンライン版）: https://claude.ai/artifact/EkyjR8X6XJdundGEXE9XgP
- 要件定義 v2: https://claude.ai/artifact/2TuwLFWA831xA7mKDd2eLP

### 構成

- 単一HTML（外部ライブラリなし、Google Fonts のみ）。黒基調・金色アクセント。
- ビュー: Home（☀️ 今日の3つ・🌱 今日の習慣を含む）/ Tasks / ノート / Calendar / World Desk / マイナー国 / Morning Mail / Inbox / 習慣 / Claude Log
- データはページに埋め込まない。オンライン版は Artifact DB（`window.claude` の `db`）から読む:
  `tools`, `memos`, `tasks`, `dues`, `prompts`（毎日の求人チェックの Claude プロンプト。経歴入りなのでリポジトリに入れない）, `notes`（Scrapbox 風のノート。[言葉] でつながる）, `habits`（罰を与えない習慣。doc ごとに `done` に日付の配列）, `memory`(doc `current`。memory/ の要約で「🧭 次は？」が読む。毎時同期で Mac から載せる), `calendar`(doc `upcoming`), `gtasks`(doc `current`),
  `reports`, `editions`, `days`, `watch`（マイナー国ウォッチの号。doc id は日付、本文はマークダウン）
- Mac版も同じ `desk/index.html` を使う。`desk/server.py`（Python標準ライブラリのみ）が
  `http://localhost:4747` で配信し、`desk/data/*.js`（`window.TOOLS` 等）をページに差し込む。
  API: `GET /api/memos|dues|tasks|prompts|notes|habits`、`POST /api/memos`、`PATCH /api/memos/<id>`、`PUT /api/dues/<id>`、`PUT /api/tasks/<id>`、`PUT /api/prompts/<id>`、`PUT /api/notes/<id>`、`PUT /api/habits/<id>`。
  データは `desk/data/*.json`（`.gitignore` 済み。個人データは絶対にコミットしない）。
- Mac の置き場所は `~/TatsukiDesk`（Documents などの保護フォルダは自動起動から読めないので不可）。
- Mac の導入: `bash desk/setup-mac.sh`（サーバー常駐＋5分ごとの `git pull origin main`）。
  ローカルから開く入口は `desk/launcher.html`（setup がデスクトップに `Tatsuki's Desk.html` としてコピー）。サーバーが動いていれば localhost:4747 へ飛び、止まっていればオンライン版を選べる。
  旧版 `~/Documents/ClaudeTools` は使わない（初回だけ memos/dues/tasks を取り込む）。
- 同期: `desk/sync/HOURLY_SYNC.md` を Mac の Claude スケジュールタスクで毎時実行。
  memos/dues/tasks/prompts/notes/habits は双方向、それ以外はオンライン → Mac。`desk/index.html` が変わっていればオンライン版にも公開する。
- 削除は `deleted:true` の論理削除。同期は `updatedAt` が新しい方を採用。
- メール送信（Gmail `send_message`）はオンライン版のみ、送信前の確認を必ず残す。
- Claude Code のコマンド（`.claude/commands/`）：`/morning-kickoff`・`/begin`（Mac 用）、`/recap`（文脈要約。標準の `/resume` と別）、`/rsd`、`/sync`・`/learn`・`/knowledge`。毎回守るルールは `.claude/rules/`。
- コミット前フック `.githooks/pre-commit`（`core.hooksPath`。SessionStart と setup-mac.sh で有効化）。実名などの禁止語は `tools/banned-words.local.txt`（git に入れない）。公開前チェック：`npm run validate-writing <file>`。
- Desk の「🧭 次は？」（右上・G キー）：何していたか（memory/ ＋進行中・最近さわったもの）と、次の1つ（今日の3つと同じ順番、次の予定まで30分未満なら短いもの）。Mac 版は `server.py` が memory/ を毎回読んで `window.MEMORY` に入れる。
- `/morning-kickoff` は Mac 用。`desk/kickoff.py` が `desk/data/` と memory/ から今日の候補を出す（読むだけ。`start <id>` だけが localhost:4747 にタスクの状態を書く）。

### 公開手順（Claude Code から）

1. `desk/index.html` を編集し、コミットして push する。Mac は `main` を自動で pull するので、
   クラウドの `claude/...` ブランチの変更は main にマージされてから Mac に届く。
2. Artifact ツールで `file_path: desk/index.html`、`url: https://claude.ai/artifact/EkyjR8X6XJdundGEXE9XgP` で publish する。
   - 先に `action: read` で最新版を読み、claude.ai 側で入った変更がないか確認してから上書きする。
   - `capabilities` は通常は渡さない（保存済みの宣言が引き継がれる）。変えるときは全部を書く：
     `{"db":{},"mcp":{"servers":[{"server":"Gmail","tools":["send_message"]}]},"sample":{}}`（`sample` は検索の「AIで広げて探す」用）。
3. 新しいURLを作らない（同じURLに上書きする）。

### ルール

- 個人データ（メール本文・予定・メモ）をリポジトリに入れない。
- 機能追加は `/desk-feature` の流れ（質問 → 要件定義 → 承認 → 実装）で行う。
- iPhone 幅 375px で横スクロールが出ないこと。
- 画面に出る文字はすべて日本語（固有名詞を除く）。データに保存する判定用の値（`priority` の `Action needed`、`region`、ツールの `group`、タスクの `status` など）は英語のままにして、表示だけ日本語にする（`PRIJA` / `REGJA` / `GROUPS`）。
  スケジュールタスク（World Desk morning brief / Morning Mail check）も日本語で書く。メールは `subject_ja`・`draft.body_ja` を足し、Gmail の下書き本体は相手の言語のまま。

## 記憶の使い方（memory/）

中断から60秒で戻れるように、作業の記憶をファイルに残す。詳しい書き方は `memory/README.md`。

- 「何してたっけ？」「続き」と言われたら：下の STATE と `memory/episodic/sessions/` の新しい3件を読み、現状・直前の決定・次の一手を5行以内で答える。根拠のファイル名を添える。
- 作業の区切り（コミットするとき・セッションを終えるとき）に、`memory/episodic/sessions/YYYY-MM-DD-話題.md`（1セッション1ファイル）を書き、必要なら `memory/STATE.md` を更新して一緒にコミットする。STATE は40行まで。
- セッション記録の先頭には「## ひとこと」（いま・次・待ち を各20字くらい、専門用語なし）を書く。Desk の「🧭 次は？」はここだけを出す。
- コミットの1行目は Desk の「🕘 この1時間にやったこと」にそのまま出る。日本語で、何をしたか分かる言葉で書く（60字まで）。
- 同じことを2回調べたら `memory/semantic/patterns/` に知識として残す。
- **このリポジトリは公開。** memory/ には技術的な中身だけを書く。メール本文・予定・メモの本文・健康のこと・経歴・実名・メールアドレス・URLの秘密部分・キーは書かない。
- 書き忘れは Stop フック（`.claude/hooks/remind-memory.sh`）が1セッション1回だけ知らせる。

@memory/STATE.md
