# Desk のアプリの窓（WKWebView）で効かないもの

- `confirm()`・`prompt()`・`alert()`：`desk/app/main.swift` に `runJavaScriptConfirmPanel` などがないので、窓は出ず、confirm は false、prompt は null を返す。確認は画面の中で作る（「もう一度押すと…」や入力欄）。2026-11-28、反省文の Publish が黙って何もしなかった。
- Web Speech（音声認識）：使えない。声は `desk/voice.py`（TatsukiMeetWatch で録音 → whisper-cli）。
- マイク・`window.print()`・ファイル保存：Desk の窓では使えない（それぞれ既定のブラウザ・`htmlpdf.swift`・`/api/utilities/file` を使う）。

# Mac で直接コミットしたときのサーバー

`desk/pull.sh` は git pull の前後で server.py と desk/*.py が変わったときだけ再起動する。Mac の `~/TatsukiDesk` で commit して push すると、pull から見て変化がないので再起動しない。`.py` を変えたら自分で `launchctl kickstart -k gui/$(id -u)/com.tatsuki.desk.server`。
