# 2026-11-14 ルーティンをスマホのアプリにする（Routine）と、最大5つ

## ひとこと
- いま：Routine アプリを公開した（Cloudflare Pages の tatsuki-routine）。Mac の Desk とスマホが合言葉で同期する。ルーティンは最大5つ
- 次：iPhone で「📱 Use on iPhone」のリンクを開いて、ホーム画面に置く。実機で音と声を確かめる
- 待ち：会社のドメインを付けるか（ドメイン名を聞く）

## やったこと
- `routine/public/index.html`：1ファイルのアプリ。ホーム（ルーティンごとのカード、今日の達成、🔥 連続日数、14日の表）、✏️ Edit（項目と、ルーティンの名前・絵文字・開始時刻・色・アラーム・削除）、設定（声の言語・声かけの間隔・音のテスト・同期の合言葉・iPhone のアラームの手順）。
- 実行画面は Desk の中から移した。☰ で全体（%・残り時間・各項目の状態）、名前を押すと次にする、Now、↑↓、項目を足す（次回も残すか選べる）、「Keep this order」で順番を保存。
- 置き場所で動きが変わる：Mac の `http://localhost:4747/routine/`（`server.py` の `routine_file`）は Desk の API を直接読み書き、それ以外は端末に保存して `/api/sync` と同期。
- 同期：`routine/functions/api/sync.js`（KV の `state` 1件、ヘッダー `x-sync-key` を Pages の secret `SYNC_KEY` と比べる）。doc ごとに新しい `updatedAt`、日付 doc はルーティンごとにチェックを足し合わせ、`<id>Run` は後に終わった方。Mac 側は `desk/routine_sync.py`（2分ごと、合言葉はキーチェーン）。
- iPhone の初回は Desk の「📱 Use on iPhone」（`GET /api/routine-link`、合言葉は `#key=` の中）。
- PWA：manifest・アイコン・`sw.js`（ページは通信優先・なければキャッシュ、アイコンはキャッシュ優先）。iOS の消音スイッチ中も鳴るよう `navigator.audioSession.type="playback"`。
- 最大5つ：`settings.lists`（id・name・emoji・at・theme・alarm）。最初の2つは id `morning`・`night` のままで前の記録を引き継ぐ。`routine_alarm.py` もルーティンごとの時刻で鳴らす。
- 確認：スマホ幅で作成・編集・実行・並べ替え・追加、Mac 版の読み込み、同期の 401 とマージ（任意のルーティン id）。

## 失敗と後始末
- マージの確認で、テスト用の日付 doc（2099-01-01）を本番の同期に送ってしまい、Mac にも届いた。KV と Mac のファイルの両方から消し、同期を1回回して戻らないことを確かめた。次からは本番の `/api/sync` に試しの doc を送らない（別の KV で試す）。

## 決めたこと
- 連続日数は本人の希望で出す（途切れたら最高記録を出し、赤や責める表示はしない）。
- 項目は公開ページの `seed.json` に入る（本人の了承済み）。`routine/dist/` は git の外。
