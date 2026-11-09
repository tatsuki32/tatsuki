# CLAUDE.md

## Tatsuki's Desk

Tatsuki の個人ダッシュボード。**正本は `desk/index.html`**（このリポジトリ）。
claude.ai のチャットで直接編集しない。変更は必ずここで行い、ここから公開する。

- 公開先（オンライン版）: https://claude.ai/artifact/EkyjR8X6XJdundGEXE9XgP
- 要件定義 v2: https://claude.ai/artifact/2TuwLFWA831xA7mKDd2eLP

### 構成

- 単一HTML（外部ライブラリ・Webフォントなし。端末の日本語ゴシック）。黒基調・金色アクセント。
- ビュー（メニューは4つのまとまり＋プロジェクト）: 今日＝Home（焦点は「今日の3つ」）/ Tasks / Calendar / 習慣、プロジェクト＝`#project/<id>`（タスクを箱に分けて見る。箱／リスト／ボード／カレンダー（月。iPhone は週の一覧）／ガント（開始日〜期日の棒、箱ごと）。箱の「＋ 足す」、チェックで完了、ドラッグで箱の移動。プロジェクトのないタスクは今までどおり）、届いたもの＝できたこと / 返信待ち / Inbox（朝のメールの画面はなくし、返信が要るメール＝要対応・返信案ありはタスクに入る。下書き・送信はタスクの詳細から）、読む＝World Desk / マイナー国、書く・振り返る＝ノート / Claude Log / ツール / Life OS / 持ち物（🎒 全部の品と重さ・値段。パッキングの✓で忘れ物を防ぐ。種類ごとの割合の棒、重い順・高い順、CSV・テキスト・PDF の書き出し）。
  iPhone は下にタブ（ホーム・タスク・カレンダー・ノート・その他）、メモは右下の丸ボタン。検索はどの画面でも `/`。
- データはページに埋め込まない。オンライン版は Artifact DB（`window.claude` の `db`）から読む:
  `tools`, `memos`, `tasks`（プロジェクトのタスクは `projectId`・`sectionId`・`group`（箱の中の小見出し）・`desc`・`estimate`・`start`（開始日 YYYY-MM-DD）・`dateLabel`（「3か月前」など表示用）・`urgent`・`order` を持つ。期日は今までどおり `due`）, `projects`（プロジェクト。`name`・`emoji`・`color`・`order`・`archived`・`lastView`）, `sections`（プロジェクトの箱。`projectId`・`name`・`color`・`order`・`when`（期間のメモ。ガントの棒にも使う）・`note`・`groups`（小見出しの配列））, `dues`, `prompts`（毎日の求人チェックの Claude プロンプト。経歴入りなのでリポジトリに入れない）, `notes`（Scrapbox 風のノート。[言葉] でつながる。ノートのタイトルは Desk 全体で自動リンクになる。`noAuto:true` で止める）, `habits`（罰を与えない習慣。doc ごとに `done` に日付の配列）, `wins`（できたこと。完了したタスクは `task-<id>`、✓した予定は `event-<id>`、自分で書いたものは `self-…`。`kind`・`title`・`date`（ホーチミンの日付）。Claude の作業は `reports` の `done` から読む）, `gear`（持ち物。1 doc が 1 品。`name`・`cat`（bag/tech/doc/wear/wash/med/other）・`grams`（1個の重さ。不明は null）・`gsrc`（spec/measured/est）・`price`（円・1個。不明は null）・`psrc`（spec=定価・買値 / est=推定）・`qty`・`note`・`url`・`img`・`gone`（手放した）・`packed`（パッキングで入れた）。`img` は Artifact の資産 id（32桁。`/_blob/<id>` で表示。オンライン版だけ）か https の URL。薬の名前はリポジトリに書かず DB だけ）, `memory`(doc `current`。memory/ の要約で「🧭 次は？」が読む。毎時同期で Mac から載せる), `calendar`(doc `upcoming`), `gtasks`(doc `current`),
  `reports`, `editions`, `days`, `watch`（マイナー国ウォッチの号。doc id は日付、本文はマークダウン）
- Mac版も同じ `desk/index.html` を使う。`desk/server.py`（Python標準ライブラリのみ）が
  `http://localhost:4747` で配信し、`desk/data/*.js`（`window.TOOLS` 等）をページに差し込む。
  API: `GET /api/memos|dues|tasks|prompts|notes|habits|lifemap|wins|gear|projects|sections`、`POST /api/memos`、`PATCH /api/memos/<id>`、`PUT /api/dues/<id>`、`PUT /api/tasks/<id>`、`PUT /api/prompts/<id>`、`PUT /api/notes/<id>`、`PUT /api/habits/<id>`、`PUT /api/lifemap/<id>`（Life OS 14）、`PUT /api/wins/<id>`、`PUT /api/gear/<id>`、`PUT /api/projects/<id>`、`PUT /api/sections/<id>`、`GET /api/followups`、`POST /api/followups/<番号>/answer`（Life OS 02。送信はしない）、`POST /api/claude`（`{cmd,arg}`。決めた13個のコマンドだけ。Terminal を開いて `claude "<コマンド>"` を始める。macOS のみ）、`GET /api/focus/state`・`POST /api/focus/start|stop`（FocusBlock への中継。パスワードは保存もログもしない）。
  データは `desk/data/*.json`（`.gitignore` 済み。個人データは絶対にコミットしない）。
- Mac の置き場所は `~/TatsukiDesk`（Documents などの保護フォルダは自動起動から読めないので不可）。
- Mac の導入: `bash desk/setup-mac.sh`（サーバー常駐＋5分ごとの `desk/pull.sh`＝`git pull origin main`、`server.py` が変わっていればサーバーを再起動）。
  ローカルから開く入口は `desk/launcher.html`（setup がデスクトップに `Tatsuki's Desk.html` としてコピー）。サーバーが動いていれば localhost:4747 へ飛び、止まっていればオンライン版を選べる。
  旧版 `~/Documents/ClaudeTools` は使わない（初回だけ memos/dues/tasks を取り込む）。
- 同期: `desk/sync/HOURLY_SYNC.md` を Mac の Claude スケジュールタスクで毎時実行。
  memos/dues/tasks/prompts/notes/habits/lifemap/wins/gear/projects/sections は双方向、それ以外はオンライン → Mac。`desk/index.html` が変わっていればオンライン版にも公開する。
- 削除は `deleted:true` の論理削除。同期は `updatedAt` が新しい方を採用。
- メール送信（Gmail `send_message`）はオンライン版のみ、送信前の確認を必ず残す。
- Claude Code のコマンド（`.claude/commands/`）：`/morning-kickoff`・`/begin`・`/kickoff`（Mac 用）、`/recap`（文脈要約。標準の `/resume` と別）、`/tsuzuki`（続きを3行で出す）・`/split`（15分以内に分けて Desk のタスクに足す）、`/rsd`、`/sync`・`/learn`・`/knowledge`、`/where`・`/lowenergy`（Life OS 16。Mac 用）。毎回守るルールは `.claude/rules/`。
- コミット前フック `.githooks/pre-commit`（`core.hooksPath`。SessionStart と setup-mac.sh で有効化）。実名などの禁止語は `tools/banned-words.local.txt`（git に入れない）。公開前チェック：`npm run validate-writing <file>`。
- Desk の「⌨️ Claude に頼む」（Home のカード・🤖 ボタン・C キー・検索欄で `/`）：Claude Code のコマンドをボタンで渡す。Mac は Terminal で claude が始まり、オンラインはコピー＋claude.ai/code。押した日は習慣 `claude-cmd` に付く。検索欄で `/` を打つと Notion 風のコマンド一覧（操作・移動・Claude コマンド・設定）。
- Desk の「🎯 集中」（Mac 版だけ）：アプリとWebサイトをブロックする `focusblock/`（root で常駐、127.0.0.1:47800）の開始・解除・残り時間。リストとスケジュールは FocusBlock の設定画面で編集。導入と更新は `focusblock/install.sh`。Claude・Claude のサイト・システム設定は常にブロックしない。
- Desk の「🧭 次は？」（右上・G キー）：何していたか（memory/ ＋進行中・最近さわったもの）と、次の1つ（今日の3つと同じ順番、次の予定まで30分未満なら短いもの）。Mac 版は `server.py` が memory/ を毎回読んで `window.MEMORY` に入れる。
- `/morning-kickoff` は Mac 用。`desk/kickoff.py` が `desk/data/` と memory/ から今日の候補を出す（読むだけ。`start <id>` だけが localhost:4747 にタスクの状態を書く）。

### 公開手順（Claude Code から）

1. `desk/index.html` を編集し、コミットして push する。Mac は `main` を自動で pull するので、
   クラウドの `claude/...` ブランチの変更は main にマージされてから Mac に届く。
2. Artifact ツールで `file_path: desk/index.html`、`url: https://claude.ai/artifact/EkyjR8X6XJdundGEXE9XgP` で publish する。
   - 先に `action: read` で最新版を読み、claude.ai 側で入った変更がないか確認してから上書きする。
   - `capabilities` は通常は渡さない（保存済みの宣言が引き継がれる）。変えるときは全部を書く：
     `{"db":{},"mcp":{"servers":[{"server":"Gmail","tools":["send_message"]}]},"sample":{},"assets":{},"downloads":true}`（`sample` は検索の「AIで広げて探す」用、`assets` は持ち物の写真の置き場、`downloads` は持ち物の CSV・テキスト保存）。
3. 新しいURLを作らない（同じURLに上書きする）。

### ルール

- 個人データ（メール本文・予定・メモ）をリポジトリに入れない。
- 機能追加は `/desk-feature` の流れ（質問 → 要件定義 → 承認 → 実装）で行う。
- UI/UX を作る・変えるときは、毎回『ノンデザイナーズ・デザインブック』を全文読んでから（`.claude/rules/design-ui.md`、ルールは `memory/semantic/patterns/design-nondesigner.md`）。
- iPhone 幅 375px で横スクロールが出ないこと。
- 画面に出る文字はすべて日本語（固有名詞を除く）。データに保存する判定用の値（`priority` の `Action needed`、`region`、ツールの `group`、タスクの `status` など）は英語のままにして、表示だけ日本語にする（`PRIJA` / `REGJA` / `GROUPS`）。
  スケジュールタスク（World Desk morning brief / Morning Mail check）も日本語で書く。メールは `subject_ja`・`draft.body_ja` を足し、Gmail の下書き本体は相手の言語のまま。

## Life OS（`lifeos/`）

18個の小さなツールの置き場。各ツールの担当は `lifeos/README.md` だけ読めば作業できる。

- `lifeos/NN-slug/tool.json` が各ツールの正本。Desk の「Life OS」画面に出る（Mac は `server.py` が毎回読んで `window.LIFEOS`、オンラインは DB の `lifeos`。毎時同期で Mac → オンラインの一方向）。
- 共通部品は `lifeos/lib/`（データ置き場・Desk タスク・追記ログ・Markdown）。Telegram の受け口は `lifeos/bot/`（`NN-slug/handler.py` を自動で読む。試すのは `python3 lifeos/bot/cli.py "テキスト"`）。
- 個人データは `desk/data/lifeos/`（`LIFEOS_DATA`）。タスクは Desk の `tasks` だけに置く。
- メモ連携（11）：Desk の「🔗 メモ連携」から動かす（Mac だけ。`GET /api/memolink`、`POST /api/memolink/run`）。毎時同期でも動く。メモは `id` が `ln-` で始まるノートになる。
- 返信待ち（02）：状態の正本は Mac の `desk/data/lifeos/tools/02-followup/state.json`。オンラインの DB `followups` は毎時同期の写しで、件名と相手のアドレスだけを載せる。iPhone の答えは `answer` 欄に置き、次の同期で Mac に反映する。Desk からメールは送らない。
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
