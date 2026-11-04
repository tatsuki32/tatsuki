# CLAUDE.md

## Tatsuki's Desk

Tatsuki の個人ダッシュボード。**Mac だけで使う**（2026-11-04 にローカル中心へ組み替え）。正本は `desk/index.html`（このリポジトリ）と Mac の `desk/data/`。
入口は「Tatsuki's Desk.app」（`desk/app/`。`~/Applications` に入り、デスクトップに別名）。Desk の専用窓で、外部リンクは既定のブラウザで開く。

- オンライン版（claude.ai の Artifact）は同期も公開もしない。ページとデータは消さずに残してある。`index.html` の `db` / `window.claude` の分岐はその名残で、次フェーズで消す。
- 要件定義（ローカル中心化）: https://claude.ai/artifact/AY3dYUjuxPTpnBjjcqDdFq

### 構成

- 単一HTML（外部ライブラリ・Webフォントなし。端末の日本語ゴシック）。黒基調・金色アクセント。
- ビュー（メニューは4つのまとまり）: 今日＝Home（焦点は「今日の3つ」）/ Tasks / Calendar / 習慣、届いたもの＝できたこと / 返信待ち / Inbox（朝のメールの画面はなくし、返信が要るメール＝要対応・返信案ありはタスクに入る。下書き・送信はタスクの詳細から）、読む＝World Desk / マイナー国、書く・振り返る＝ノート / Claude Log / ツール。
  iPhone は下にタブ（ホーム・タスク・カレンダー・ノート・その他）、メモは右下の丸ボタン。検索はどの画面でも `/`。
- データの置き場（すべて `desk/data/`）：
  `memos`, `tasks`, `dues`, `prompts`（毎日の求人チェックの Claude プロンプト。経歴入りなのでリポジトリに入れない）, `notes`（Scrapbox 風のノート。[言葉] でつながる。ノートのタイトルは Desk 全体で自動リンクになる。`noAuto:true` で止める）, `habits`（罰を与えない習慣。doc ごとに `done` に日付の配列）, `wins`（できたこと。完了したタスクは `task-<id>`、✓した予定は `event-<id>`、自分で書いたものは `self-…`。`kind`・`title`・`date`（ホーチミンの日付）。Claude の作業は `reports` の `done` から読む）, `lifemap` は `<名前>.json`。
  `tools`, `calendar`(doc `upcoming`), `gtasks`(doc `current`), `reports`, `editions`, `days`, `watch`（マイナー国ウォッチの号。doc id は日付、本文はマークダウン）は `docs/<名前>/<id>.json` が正本で、ページ用の `*.js` はそこから作る（`server.py` の `FEEDS`）。
- `desk/server.py`（Python標準ライブラリのみ）が
  `http://localhost:4747` で配信し、`desk/data/*.js`（`window.TOOLS` 等）をページに差し込む。
  API: `GET /api/memos|dues|tasks|prompts|notes|habits|lifemap|wins`、`POST /api/memos`、`PATCH|PUT /api/memos/<id>`、`PUT /api/tools/<id>`（`urlEditable` のツールの URL だけ）、`PUT /api/dues/<id>`、`PUT /api/tasks/<id>`、`PUT /api/prompts/<id>`、`PUT /api/notes/<id>`、`PUT /api/habits/<id>`、`PUT /api/lifemap/<id>`（Life OS 14）、`PUT /api/wins/<id>`、`GET /api/followups`、`POST /api/followups/<番号>/answer`（Life OS 02。送信はしない）、`POST /api/claude`（`{cmd,arg}`。決めた14個のコマンドだけ。Terminal を開いて `claude "<コマンド>"` を始める。macOS のみ）、`GET /api/focus/state`・`POST /api/focus/start|stop`（FocusBlock への中継。パスワードは保存もログもしない）。
  データは `desk/data/*.json`（`.gitignore` 済み。個人データは絶対にコミットしない）。
- Mac の置き場所は `~/TatsukiDesk`（Documents などの保護フォルダは自動起動から読めないので不可）。
- Mac の導入: `bash desk/setup-mac.sh`（アプリのビルド＋サーバー常駐＋5分ごとの `git pull origin main`＋15分ごとのデータ保管）。
  ブラウザで開くときの入口は `desk/launcher.html`。
  旧版 `~/Documents/ClaudeTools` は使わない。
- データの保管: `desk/data/` は独立した Git リポジトリで、`desk/backup_data.py`（launchd、15分ごと）が非公開リポジトリへ送る。キーらしい文字列があれば送らずに止まる。公開リポジトリには入れない。
- 定期タスク: `desk/sync/SCHEDULED_TASKS.md`。Mac の Claude スケジュールタスクが `desk/sync.py put` で `desk/data/` に書く。Claude アプリが閉じていた間の分は、次に開いたときに動く。
- 削除は `deleted:true` の論理削除。
- メール送信は Desk からしない。タスク詳細の「Claude に送信を頼む」が `/mail-send` を Terminal で始め、Claude が会話で承認を得てから送る。
- Claude Code のコマンド（`.claude/commands/`）：`/morning-kickoff`・`/begin`・`/kickoff`（Mac 用）、`/recap`（文脈要約。標準の `/resume` と別）、`/tsuzuki`（続きを3行で出す）・`/split`（15分以内に分けて Desk のタスクに足す）、`/rsd`、`/sync`・`/learn`・`/knowledge`、`/where`・`/lowenergy`（Life OS 16。Mac 用）、`/mail-send`（Desk から起動するメール送信）。毎回守るルールは `.claude/rules/`。
- コミット前フック `.githooks/pre-commit`（`core.hooksPath`。SessionStart と setup-mac.sh で有効化）。実名などの禁止語は `tools/banned-words.local.txt`（git に入れない）。公開前チェック：`npm run validate-writing <file>`。
- Desk の「⌨️ Claude に頼む」（Home のカード・🤖 ボタン・C キー・検索欄で `/`）：Claude Code のコマンドをボタンで渡す。Terminal で claude が始まる。押した日は習慣 `claude-cmd` に付く。検索欄で `/` を打つと Notion 風のコマンド一覧（操作・移動・Claude コマンド・設定）。
- Desk の「🎯 集中」：アプリとWebサイトをブロックする `focusblock/`（root で常駐、127.0.0.1:47800）の開始・解除・残り時間。リストとスケジュールは FocusBlock の設定画面で編集。導入と更新は `focusblock/install.sh`。Claude・Claude のサイト・システム設定は常にブロックしない。
- Desk の「🧭 次は？」（右上・G キー）：何していたか（memory/ ＋進行中・最近さわったもの）と、次の1つ（今日の3つと同じ順番、次の予定まで30分未満なら短いもの）。`server.py` が memory/ を毎回読んで `window.MEMORY` に入れる。
- `/morning-kickoff` は Mac 用。`desk/kickoff.py` が `desk/data/` と memory/ から今日の候補を出す（読むだけ。`start <id>` だけが localhost:4747 にタスクの状態を書く）。

### ルール

- 個人データ（メール本文・予定・メモ）をリポジトリに入れない。
- 機能追加は `/desk-feature` の流れ（質問 → 要件定義 → 承認 → 実装）で行う。
- UI/UX を作る・変えるときは、毎回『ノンデザイナーズ・デザインブック』を全文読んでから（`.claude/rules/design-ui.md`、ルールは `memory/semantic/patterns/design-nondesigner.md`）。
- iPhone 幅 375px で横スクロールが出ないこと。
- 画面に出る文字はすべて日本語（固有名詞を除く）。データに保存する判定用の値（`priority` の `Action needed`、`region`、ツールの `group`、タスクの `status` など）は英語のままにして、表示だけ日本語にする（`PRIJA` / `REGJA` / `GROUPS`）。
  スケジュールタスク（World Desk morning brief / Morning Mail check）も日本語で書く。メールは `subject_ja`・`draft.body_ja` を足し、Gmail の下書き本体は相手の言語のまま。

## Life OS（`lifeos/`）

18個の小さなツールの置き場。各ツールの担当は `lifeos/README.md` だけ読めば作業できる。

- `lifeos/NN-slug/tool.json` が各ツールの正本。Desk の「Life OS」画面に出る（`server.py` が毎回読んで `window.LIFEOS`）。
- 共通部品は `lifeos/lib/`（データ置き場・Desk タスク・追記ログ・Markdown）。Telegram の受け口は `lifeos/bot/`（`NN-slug/handler.py` を自動で読む。試すのは `python3 lifeos/bot/cli.py "テキスト"`）。
- 個人データは `desk/data/lifeos/`（`LIFEOS_DATA`）。タスクは Desk の `tasks` だけに置く。
- メモ連携（11）：Desk の「🔗 メモ連携」から動かす（`GET /api/memolink`、`POST /api/memolink/run`）。毎時の定期タスクでも動く。メモは `id` が `ln-` で始まるノートになる。
- 返信待ち（02）：状態の正本は Mac の `desk/data/lifeos/tools/02-followup/state.json`。Desk からメールは送らない。
- 続き復元（15）：`Stop` と `SessionEnd` のフック（`lifeos/15-resume/hook.py`）が、やったことと次の一手を `logs/work.jsonl` に書く。`/tsuzuki` がそれを読む。
- テスト：`python3 -m unittest discover -s lifeos/tests -t .`

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
