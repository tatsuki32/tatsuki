## ひとこと
- いま：写真で細かい記録が取れる
- 次：レシートと Drive を試す
- 待ち：本人の写真

# Life Dashboard を Desk へ（最初の版）

- 要件定義：https://claude.ai/artifact/HaH1MhDXEk4aMYv5nUZeYr
- 追加：`desk/lifelog.py`（待ち行列・読み取り・検証・照合）、`lifeos/tests/test_desk_lifelog.py`。ボットに写真の受け取りとキーチェーン対応。launchd `com.tatsuki.desk.telegram`。定期タスク `desk-lifelog-drive`。
- 画面：体（新設）、習慣の中の朝夜ルーティンとご褒美カード、お金の領収書タブ、ホームの小さな入口。
- 決定：Mac の Python は 3.9 で SDK が入らないので、API は urllib で呼ぶ（他の取り込みと同じ）。
- 過去データ：シートを1回だけ取り込み（取り込み用スクリプトは個人データを含むのでリポジトリに入れない）。GAS の時間帯のずれで 17:00 になった日付は翌日に直した。`routine log` の0%は取り込まない。
- 分かったこと：Drive のダウンロードは大きいと結果がファイルに保存されるので、`lifelog.py add-json` で受ける。
- 本番確認：睡眠のスクショと食事の写真を Telegram から読めた。本人の希望で、項目をシートの細かさまで広げ、筋トレを足した（`KINDS`）。
- 残り：レシートと Drive の実物確認。足した支出は Wise の合計に入らない。次の版は助言、人脈、連絡リマインドなど。GAS のトリガーは動作確認後に本人が止める。
