# 2026-11-21 Transcribe の話者分け

## ひとこと
- いま：会議と対面で話者を分けられる
- 次：画面収録を許可して会議で試す
- 待ち：本人の許可と本物の会議

## やったこと
- 要件定義：https://claude.ai/artifact/QXiXRg91V1fs6nwxtAX3GU （Meeting は Mac の音を録る。会議ボットは作らない）
- `desk/app/audiotap.swift`（`~/Applications/TatsukiAudioTap.app`）：ScreenCaptureKit で会議アプリとブラウザの音だけを 16kHz モノラルで `them.pcm` に。`open` で起動して許可をアプリ自身に付ける。`alive` が60秒止まるか `stop` で終わる。再起動時は前の `started` を引き継いで無音で埋める。
- `desk/transcribe/server.py`：mode（mic / meeting / inperson）、トラックごとの plan・chunks（`plan-them.json`・`chunks-them/`、行の key は `t` で始まる）、`them_offset`（tap の `started` − ページの `mic_start`）、`mix.pcm`（audioop）、`drop_echo`、sherpa-onnx の `diarize`（先に話した人が A）、`speakers`・`speakers.json`、`transcript_lines`（同じ人の行をまとめる。ja・zh は空白なしでつなぐ）。
- 録音画面：モードの切り替え、Them の音量の棒、許可がないときの1行、名前のメニュー（名前を変える・この行を別の人に）、Swap A and B、Split into 2 speakers、Copy all の形。
- `setup.sh`：sherpa-onnx v2.0.2（osx-arm64-shared-no-tts）と pyannote segmentation 3.0、3D-Speaker CAM++ zh_en。話者分けの1本以外は消して約65MB。

## 確かめたこと
- `python3 -m unittest desk.tests.test_transcribe`（偽のエンジンで8件）。
- 本物の whisper と sherpa-onnx で、2人の英語の見本が A / B に正しく分かれた（16秒を約1秒で分けた）。
- 375px で横スクロールなし。名前の変更・行の付け替え・Swap・書き出しを画面で確認。
- 本物の TatsukiAudioTap は起動して `permission` を返すところまで。実際の会議の音は未確認。

## ハマりどころ
- サンドボックスの中の `swiftc` は ScreenCaptureKit のモジュールキャッシュを書けない（`-module-cache-path` で回避）。
- 偽のエンジンで Me と Them の文が似ていると `drop_echo` が Me を消す（試験の文は違うものにする）。

## 追加：相手の声の切り替え（同じ日）
- Meeting に「Their voice」：This Mac（Mac の音を録る）／The room（iPhone のスピーカー・対面。マイクだけ）。録音前も録音中も。
- `src_log` に切り替えの時刻（マイクの秒）。room に切り替えると `stop` を置き、`tend_taps` は立ち上げ直さない。mac に戻すと `start_tap`（止めている途中なら待ってから）。アプリは前の `started` を引き継いで無音で埋めるので時刻はずれない。
- room の間のマイクだけ `diarize` の結果で Me / Them。どちらが Me かは This Mac の間のマイクの行と重なる声（`room_me_voice`）。
- 試験10件。ブラウザの枠はマイクを使えないので、切り替えは API で確かめた。

## 追加：文から議事録（同じ日）
- `desk/transcribe/docs.py`：Word などは `textutil`、PDF は `reader.pdf_text`、Docs は Drive API（`drive.readonly`、client はメール整理と共用、refresh は `tatsuki-desk-gdrive`）。議事録は `claude-sonnet-5-5` の構造化出力＋`fallbacks: "default"`。
- 本物の API で見本の会議から議事録が出た（入力877・出力179トークン、約0.5円）。
- ハマりどころ：サーバーに `do_PUT` が無く、議事録の直しが 501 で捨てられていた。テストサーバーは `/tmp` の置き場なのでキーチェーンを読まず、議事録は「キーがない」になる（正しい動き）。サンドボックスの中では `textutil` が docx を書けない（試験は外で通す）。
