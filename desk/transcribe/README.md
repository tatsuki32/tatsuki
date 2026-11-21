# 文字起こし（長時間録音ツール）

Macだけで動く録音・文字起こしツールです。時間制限と回数制限はなく、音声も文字もこのMacの外には出ません。

## 使い方

1. Desk の左メニュー「Write & reflect」の「🎙️ Transcribe」を開き、「Record or import ↗」を押します。録音画面がブラウザで開きます。
2. 初回だけマイクの使用を許可します。Macのシステム設定の「プライバシーとセキュリティ > マイク」でブラウザの許可も必要です。
3. 「Record」を押します。終わったら「Stop」を押すと、自動で文字起こしが始まります。
4. 文字はその場で手直しできます。「Copy all」か「Save as text」で取り出せます。

iPhoneの録音は、ボイスメモをAirDropでMacに送り、画面にドロップすれば取り込めます。
録音サーバー（127.0.0.1:8791）は Desk のサーバーが起動時に立ち上げ、止まったら1分以内に立ち上げ直します（`desk/transcribe_desk.py`）。
初めての Mac では `bash desk/transcribe/setup.sh` を1回実行してエンジン（約1.6GB）を入れます。

## 話者を分ける（2026-11-21）

録音画面の上で3つから選びます。選んだものは次回も残ります。

| 選ぶもの | 録るもの | 分け方 |
|---|---|---|
| Mic only | マイク1本 | 分けない（前と同じ） |
| Meeting | マイク＝Me、Mac の音＝Them | 2本を別々に文字にして時刻で並べる |
| In person | マイク1本 | 文字にしたあと、声の特徴で Speaker A / B に分ける |

- Meeting の Mac の音は `~/Applications/TatsukiAudioTap.app`（`desk/app/audiotap.swift`）が録ります。Zoom・ブラウザ（Meet）・Teams・FaceTime の音だけで、通知音や音楽は入りません。初回だけ「システム設定 › プライバシーとセキュリティ › 画面とシステムオーディオの録音」で Tatsuki Audio Tap を許可します。画面の絵は残しません。
- 相手に録音することを伝えてください。スピーカーだと相手の声がマイクにも入るので、イヤホンを使います（同じ時刻・同じ文の Me の行は自動で消します）。
- 名前を押すと「名前を変える」（全部の行）と「この行を別の人にする」。In person は「Swap A and B」も。前に録った録音や取り込んだ録音は「Split into 2 speakers」で後から分けられます。
- Meeting には「Their voice」のスイッチがあります。This Mac＝Zoom・Meet を Mac で使う（Mac の音を録る。イヤホンでも録れる）、The room＝iPhone のスピーカーでの通話や対面（Mac の音は録らず、マイクを声で2人に分けて Me / Them を推測）。録音前にも録音中にも切り替えられ、切り替えた時刻から効きます。推測が逆なら「Swap Me and Them (room parts)」。
- Copy all と Save as text は「名前: 発言」で、同じ人が続く行は1つにまとめます。
- 話者分けは sherpa-onnx（pyannote の区間検出＋3D-Speaker CAM++、約65MB、`engine/diar/`）。Mac の中だけで動きます。

## 文から議事録（2026-11-21）

録音していない会議のまとめも、同じ一覧に入れて議事録にできます。

- 入れ方：録音画面の下に Word・PDF・テキスト・Markdown を落とす（何個でも）、「Paste text」で貼る、「Google Docs link」で Docs のリンクを貼る。
- Google Docs は初回だけ「Connect Google Drive」で読むだけの許可を出します（メール整理と同じ Google Cloud の仕組み。Drive には何も書きません）。テスト中のアプリなので、7日ほどで切れたら押し直します。
- 文字を取り出すのは Mac の中です（Word は `textutil`、PDF は Reader と同じ `pdftext`）。元のファイルは残しません。画像だけの PDF は読めません。
- 「Make minutes」を押したときだけ、文（録音なら文字起こし）を Claude（Sonnet 5.5）に送り、要点・決まったこと・やること・次に話すことにします。1件数円。音声は送りません。議事録はその場で直せ、「Copy minutes」でコピー、やることは「Make it a task」で Desk のタスクになります。
- 一覧の上の検索は、題・本文・文字起こし・議事録から探します。会議の日は題の下の日付で直せます。

## 長時間使うときの注意

- 録音にはChromeをおすすめします（動作確認に使ったブラウザです）。
- Macのふたは閉じないでください。録音中と文字起こし中はスリープを止めていますが、ふたを閉じると止まることがあります。
- 録音中は電源につないでおくと安心です。
- 録音のタブは閉じないでください。別のタブやアプリを使うのは問題ありません。

## 止まったときの動き

| 起きたこと | どうなるか |
|---|---|
| ブラウザが落ちた | 数秒前までの音声は保存済みです。記録を開いて「Transcribe what was saved」を押します |
| 録音中にサーバーが落ちた | 音声はブラウザが持ち続け、復帰後に再送します。Desk が1分以内に立ち上げ直します |
| 文字起こし中にサーバーやMacが止まった | 次の起動時に、終わっていない区間から自動で再開します |
| ある区間の処理に失敗した | 2回再試行し、次にCPU処理に切り替えます。それでも駄目ならその区間だけ失敗と表示し、「Retry failed parts」でやり直せます |

## 仕組み

- 録音は16kHz・モノラルで保存します。容量は1時間あたり約58MBです。
- 音声認識は whisper.cpp と large-v3-turbo モデルをMacのGPUで動かします。無音の検出には Silero VAD を使います。
- 長い音声は約5分ごとに無音の位置で分け、1区間ずつ別プロセスで処理します。区間をまたいで誤認識が連鎖しにくくなります。
- 接続はこのMacの中からだけ受け付けます。

## ファイル

- `desk/data/transcribe/sessions/` … 録音と文字起こしの結果（git にも非公開の保管にも入れない）
- `desk/transcribe/engine/` … エンジンとモデル（git に入れない）。`setup.sh` で作り直せます
- `desk/transcribe/server.py`, `public/` … 本体（Python標準ライブラリのみ）
- `desk/transcribe/docs.py` … 文の取り出し・Google Docs・議事録（`python3 docs.py connect|status`）
- `desk/app/audiotap.swift` … 会議の相手の声を録るアプリ。作り直すと許可が外れるので、`setup.sh` は無いときだけ作る
- 試験：`python3 -m unittest desk.tests.test_transcribe`、画面は `.claude/launch.json` の `transcribe-test`（8792。Mac の音は録らない）
