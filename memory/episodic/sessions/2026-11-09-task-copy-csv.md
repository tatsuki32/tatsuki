# 2026-11-09 タスクをテキストでコピー・プロジェクトを CSV で保存

## ひとこと
- いま：タスク1件を文字でコピー、プロジェクトを CSV で保存できる
- 次：本番サーバーを再起動して、Mac のアプリで CSV の保存を試す
- 待ち：なし

## やったこと
- `desk/index.html`：タスクの画面に「Copy as text」。開いている画面の入っている欄だけを `欄の名前: 値` で並べる（`taskFormText`。空の欄は出さない。保存前の入力もそのまま入る）。
- プロジェクトの見出しに「Export CSV」（`projCsv`・`exportProjCsv`）。箱の順 → 箱の中の順。列は箱・小見出し・題・状態・期日・時刻・日付ラベル・開始日・所要時間・説明・重要・メモ・ID。BOM 付き UTF-8。
- Desk の窓（WKWebView）は自分でファイルを保存できないので、`POST /api/utilities/file`（`utilities.text_file`。csv・txt、5MB まで、1時間メモリに置く）→ `/api/utilities/file/<token>` を既定のブラウザがダウンロードする（`saveFile`）。オンライン版は今までの `saveText`。
- 試験サーバー（desk-test）の本物のデータで確認：テキスト・CSV（49件）・ファイル名の日本語。

## 残り
- `server.py` を変えたので、本番の 4747 は再起動が要る（`launchctl kickstart -k gui/$(id -u)/com.tatsuki.desk.server`）。
- 375px は表示の切り替えが試験の窓で効かず、CSS（`.head`・`.row` が折り返す）で確認した。
