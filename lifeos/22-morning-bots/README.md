# 22 Morning Log Bots（朝の記録ボット2つ）

毎朝4時（いる場所の時計）に Telegram の2つのボットが聞く。答えは Google スプレッドシートに入り、Mac の Desk が1日1回取り込む。Mac が閉じていても記録できる。
要件定義：https://claude.ai/artifact/7M9jcYFCYoaw5Wro11ps4t

- **Sleep & Steps**：「7時間半 8200歩」かスクショを送ると、すぐ保存。返事に Undo。
- **Meals**：写真か文を送ると、すぐ保存（2026-11-28 から。前は案 → Save）。Desk と同じ17の栄養素を全部推定し、返事にこの食事の各栄養素が1日の目標の何％か、その日の合計／目標・残り・超えた分を出す。6時間以内の直し（「ご飯は半分」）は前の食事を置き換え、Undo・Cancel で消す。
- 目標値：Mac の Desk が `body/targets.json`（17の栄養素・歩数・睡眠）を時間帯と一緒に送る（`op=tz` の `targets`、変わった日と毎日1回）。睡眠と歩数の返事にも目標の何％かが出る。
- 時間帯：Mac の Desk が毎日 Mac の時間帯を送る。Telegram で位置を送るか `/tz Asia/Tokyo` でも変わる（位置は保存しない）。
- ボットの文はアメリカ英語。日本語で答えてよい。

## 仕組み
- `Code.js` は Google Apps Script（スプレッドシートに付ける）。1分ごとに Telegram を見に行き（getUpdates）、4:00〜12:00 にまだなら問いを1回送る。読むのは Claude Haiku 5.5（構造化出力）。
- シート：`Sleep & Steps`（id・date・kind sleep|steps・sleep_min・bedtime・wake_time・steps・text・saved_at・imported_at・deleted）、`Meals`（id・date・meal・items・kcal と17の栄養素・text・…。列は見出しの名前で探すので、前のシートには右端に足される）、`Settings`（今の時間帯）。
- 答えるのは最初に `/start` した人だけ。ほかの人には何も返さない。
- Desk 側は `desk/lifebots.py`（4747 のサーバーが10分ごと。取り込みは Mac の時計で 5:00 を過ぎた最初の回と「Import now」）。Web アプリの `?op=export|ack|tz`、合言葉は Script Properties の `DESK_KEY` と Mac のキーチェーン `tatsuki-desk-lifebots`。記録は体の `records.json` に `mb-<行id>`（source `morning-bots`）。

## 入れ方（本人が1回、約10分）
1. https://sheets.new で空のスプレッドシートを作り、名前を「Life Log Bots」にする。
2. 拡張機能 → Apps Script。中身を全部消して `Code.js` を貼り、保存。
3. Telegram の @BotFather で `/newbot` を2回（例：Sleep & Steps 用と Meals 用）。それぞれのトークンを控える。
4. Apps Script の歯車（プロジェクトの設定）→ スクリプト プロパティに3つ足す：`SLEEP_TOKEN`、`MEALS_TOKEN`、`ANTHROPIC_KEY`（Anthropic の API キー）。
5. 上の関数の選択で `setup` を選んで実行 → 許可（「確認されていないアプリ」は「詳細」→「移動」）。実行ログに Desk key が出る。
6. デプロイ → 新しいデプロイ → 種類「ウェブアプリ」、実行ユーザー「自分」、アクセス「全員」→ URL を控える（合言葉がないと何も返さない）。
7. Telegram で両方のボットに `/start`。
8. Mac で `python3 desk/lifebots.py setup <6の URL>` → 5の Desk key を貼る（キーチェーンに入る）。

`Code.js` を直したら、Apps Script に貼り直して「デプロイを管理」で新しいバージョンにする。

## 試験
`node --test lifeos/22-morning-bots/test/*.mjs`（偽の Telegram・Sheets・Claude）、`python3 -m unittest desk.tests.test_lifebots`。
