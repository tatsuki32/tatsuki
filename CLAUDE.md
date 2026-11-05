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
  `memos`, `tasks`, `dues`, `prompts`（毎日の求人チェックの Claude プロンプト。経歴入りなのでリポジトリに入れない）, `notes`（Scrapbox 風のノート。[言葉] でつながる。ノートのタイトルは Desk 全体で自動リンクになる。`noAuto:true` で止める。ページにはその言葉を含む文がカードで並ぶ（`sentencesFor`。全ページが対象で、保存しない）。ニュースとマイナー国は自動リンクせず、文字を選んでリンクした記事の文だけがカードになる。タスクの表示も自動リンクしない（`data-noauto`）。「出典：」の行は表示と編集から外してページの下に出し、「外す」で消せる）, `habits`（罰を与えない習慣。doc ごとに `done` に日付の配列）, `wins`（できたこと。完了したタスクは `task-<id>`、✓した予定は `event-<id>`、自分で書いたものは `self-…`。`kind`・`title`・`date`（ホーチミンの日付）。Claude の作業は `reports` の `done` から読む）, `lifemap` は `<名前>.json`。
  `tools`, `calendar`(doc `upcoming`), `gtasks`(doc `current`), `reports`, `editions`, `days`, `watch`（マイナー国ウォッチの号。doc id は日付、本文はマークダウン）は `docs/<名前>/<id>.json` が正本で、ページ用の `*.js` はそこから作る（`server.py` の `FEEDS`）。
- `desk/server.py`（Python標準ライブラリのみ）が
  `http://localhost:4747` で配信し、`desk/data/*.js`（`window.TOOLS` 等）をページに差し込む。
  API: `GET /api/memos|dues|tasks|prompts|notes|habits|lifemap|wins`、`POST /api/memos`、`PATCH|PUT /api/memos/<id>`、`PUT /api/tools/<id>`（`urlEditable` のツールの URL だけ）、`PUT /api/dues/<id>`、`PUT /api/tasks/<id>`、`PUT /api/prompts/<id>`、`PUT /api/notes/<id>`、`PUT /api/habits/<id>`、`PUT /api/lifemap/<id>`（Life OS 14）、`PUT /api/wins/<id>`、`GET /api/whatsapp`、`GET /api/followups`、`POST /api/followups/<番号>/answer`（Life OS 02。送信はしない）、`POST /api/claude`（`{cmd,arg}`。決めた17個のコマンドだけ。Terminal を開いて `claude "<コマンド>"` を始める。macOS のみ）、`GET /api/focus/state`・`POST /api/focus/start|stop`（FocusBlock への中継。パスワードは保存もログもしない）、`GET /api/wise?period=week|month|3m|6m|all|custom`・`GET /api/wise/export?format=csv|pdf&kind=list|report`・`PUT /api/wise/category|kind|recurring|seen|upcoming-hide|direct|budget|trips/<id>|goals|sims|learn/<id>|close/<月>|limits|recon/<月>`・`POST /api/wise/subs`（「お金」の画面。Wise には問い合わせずファイルを読む）。
  データは `desk/data/*.json`（`.gitignore` 済み。個人データは絶対にコミットしない）。
- Mac の置き場所は `~/TatsukiDesk`（Documents などの保護フォルダは自動起動から読めないので不可）。
- Mac の導入: `bash desk/setup-mac.sh`（アプリのビルド＋サーバー常駐＋5分ごとの `git pull origin main`＋15分ごとのデータ保管）。
  ブラウザで開くときの入口は `desk/launcher.html`。
  旧版 `~/Documents/ClaudeTools` は使わない。
- データの保管: `desk/data/` は独立した Git リポジトリで、`desk/backup_data.py`（launchd、15分ごと）が非公開リポジトリへ送る。キーらしい文字列があれば送らずに止まる。公開リポジトリには入れない。
- 定期タスク: `desk/sync/SCHEDULED_TASKS.md`。Mac の Claude スケジュールタスクが `desk/sync.py put` で `desk/data/` に書く。Claude アプリが閉じていた間の分は、次に開いたときに動く。
- 削除は `deleted:true` の論理削除。
- タスクの状態は `notyet` / `doing` / `done` / `paused`（一旦中止）/ `archived`。一旦中止とアーカイブはタスク画面の見出し下の小さな入口から見る。
- タスクの時期と並び替え：各列をトグルで分ける（未着手・進行中は 今日／未分類／今週／今月／来月／再来月／年／もっと先、完了は 今日／今週／今月／それより前）。時期は「本人の選択 `planDate`（期間の最後の日）→ 期日 → 未分類」で決まり、過ぎたら今日に入る。Claude の案 `planAi` と目安 `estAi` は毎朝の定期タスク `desk-task-estimate`（`desk/task_estimate.py`）が付け、案は押すまで動かない。本人の所要時間は `estMin`。並び替えと開閉は端末に覚える。未分類は「仕分ける」（`openTriage`。タスク画面の見出し・ホーム・`/` 一覧）で1枚ずつ決められる。10枚で区切り。ボタン・数字キー・Enter と →（案のとおり）・←（あと回し）・Z（戻す）・左右のドラッグ。
- タスクをやりやすくする：`clear`（具体的なタイトル・まず5分・手順・目印・出典・質問・頼む文面）を毎晩の定期タスク `desk-task-clarify`（1晩30件）と `/clarify <id>` が書く（`desk/task_clarify.py`）。元の `title` は上書きしない（「元に戻す」は `clear.titleOff`）。本人の答えが入ると `clear.redo` で次の夜の先頭へ。質問は1問ずつのカード `openQuestions`。友達への文面は作ってコピーするだけで送らない。「Claude と」は押すとタスクの内容を文章にしてコピーする（`claudePrompt`）。古い画面が `clear` を消さないよう `server.py` の `keep_clear` が守る。文体の基準は `desk/data/clarify-style.md`（個人データなのでリポジトリに入れない）。
- タスクをカレンダーへ（`desk/task_calendar.py`、毎時の定期タスク `desk-task-calendar`）：未着手で時刻つきの期日のものを、3日前の同じ時刻に primary カレンダーへ入れる（灰色・先頭に ☑️・空き時間・通知なし）。一方向。進行中は残す。完了・一旦中止・アーカイブ・削除で消す。日付だけの期日は 23:59（ホーチミン）で持ち、入れない。☑️ の予定は Desk のカレンダーに取り込まない。対応表は `desk/data/calendar-sync.json`。
- 連絡返信（定期タスク `desk-morning-mail`、3:00・16:00）：Gmail は `sync.py mail-add` で当日分に足す。返信済みは `sync.py mail-replied`。WhatsApp は `desk/whatsapp_sync.py` が Mac の `ChatStorage.sqlite` を読むだけで、1対1の返信待ちを `source: whatsapp` のタスク（`連絡返信：名前`、1回のやりとりで1件）にする。こちらが返すと次の取り込みで完了。送信はしない。本文と返信案は `desk/data/whatsapp/items.json`（GitHub の保管から外す。`GET /api/whatsapp`）。どちらも時期は取り込んだ日の「今日」。
- メール送信は Desk からしない。タスク詳細の「Claude に送信を頼む」が `/mail-send` を Terminal で始め、Claude が会話で承認を得てから送る。
- Claude Code のコマンド（`.claude/commands/`）：`/morning-kickoff`・`/begin`・`/kickoff`（Mac 用）、`/recap`（文脈要約。標準の `/resume` と別）、`/tsuzuki`（続きを3行で出す）・`/split`（15分以内に分けて Desk のタスクに足す）、`/rsd`、`/sync`・`/learn`・`/knowledge`、`/where`・`/lowenergy`（Life OS 16。Mac 用）、`/mail-send`（Desk から起動するメール送信）、`/money-review`（お金の助言）。毎回守るルールは `.claude/rules/`。
- コミット前フック `.githooks/pre-commit`（`core.hooksPath`。SessionStart と setup-mac.sh で有効化）。実名などの禁止語は `tools/banned-words.local.txt`（git に入れない）。公開前チェック：`npm run validate-writing <file>`。
- Desk の「⌨️ Claude に頼む」（Home のカード・🤖 ボタン・C キー・検索欄で `/`）：Claude Code のコマンドをボタンで渡す。Terminal で claude が始まる。押した日は習慣 `claude-cmd` に付く。検索欄で `/` を打つと Notion 風のコマンド一覧（操作・移動・Claude コマンド・設定）。
- Desk の「🎯 集中」：アプリとWebサイトをブロックする `focusblock/`（root で常駐、127.0.0.1:47800）の開始・解除・残り時間。リストとスケジュールは FocusBlock の設定画面で編集。導入と更新は `focusblock/install.sh`。Claude・Claude のサイト・システム設定は常にブロックしない。
- Desk の「💰 お金」：Wise の決済を読むだけで取り込む（`desk/wise_sync.py`、launchd で毎朝7時とログイン時）。送金や支払いの処理は持たない。明細は `desk/data/wise/` に置き、GitHub の保管から外す（`backup_data.py` の `IGNORE`）。トークンは macOS のキーチェーン（サービス名 `tatsuki-desk-wise`）。Claude は `python3 desk/wise_sync.py summary [YYYY-MM]` の JSON を読んで答える。合計は USD 基準の入金・出金・差引。Wise に USD の記録がなければ、決済時刻の Wise のレートで換算する（`rates.json` に1件ずつ保存）。支払った通貨も一覧と通貨別の合計に出す。取り消しと自分の残高どうしの両替は入れない。分析と CSV・PDF は `desk/wise_report.py`（PDF は `desk/app/htmlpdf.swift` を初回に自動ビルド）。解説は原典を確かめた一般論だけで、個別の投資助言はしない。画面は3つのまとまり（見る＝概要・グラフ・分析・明細・世界、決める＝予算・旅の計画・貯金、学ぶ＝助言・シミュレーション・毎日の学び）。分析と助言は選んだ期間で計算し直す。機能は更新してよいが消さない。計算は `desk/wise_analysis.py`。明細の種類（支出・収益・自分の移動・立替・返金・未確認）と分類は、本人の選択（`flows.json`・`categories.json`）→ Claude の案（`guess.json`）→ 型の順で決まる。助言は `/money-review`（3日ごとの定期タスク `desk-money-review`）が `advice.json` に書き、計算にない数字や責める言葉を含むものは保存しない。「これからの支出」と豆知識は `desk/wise_plan.py`（定期タスク `desk-money-upcoming`・`desk-money-tips`）。メール本文は保存せず、USD と現地費の目安はコードが計算する。生年月日は `desk/data/wise/profile.json`。予算・旅の計画・貯金・シミュレーション・毎日の学びは `desk/wise_goals.py`（`budgets.json`・`trips.json`・`goals.json`・`sims.json`・`lessons.json`、定期タスク `desk-money-learn`）。旅の計画は手入力で、Wise の明細とは切り離す。シミュレーションの利回りに初期値は入れない。「世界」タブは世界銀行 ICP 2021 の公開統計（`desk/wise_world.py`、`world.json`、取り込みのときに30日ごとに更新。`GET /api/wise/world`）。最低賃金は ILO を自動で取れず、Wikipedia の一覧から取る（公的統計と分けて表示）。銀行から直接払った送金は `direct.json` で残高の照合から外す（支出には残す）。
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
