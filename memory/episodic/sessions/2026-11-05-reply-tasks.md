# 2026-11-05 連絡返信タスク（Gmail と WhatsApp）

## ひとこと
- いま：返信待ちがタスクに入る
- 次：16時の自動実行を確かめる
- 待ち：main への取り込み

## やったこと
- `desk/whatsapp_sync.py` を追加。Mac の `ChatStorage.sqlite` を読むだけで、1対1の返信待ちを `source: whatsapp` のタスクにする。
- 定期タスク `desk-morning-mail` を 3:00・16:00 にし、Gmail と WhatsApp を両方見る形にした。
- `sync.py` に `mail-add`（当日分に足す）と `mail-replied`（返信済みで閉じる）を追加。
- 本文と返信案は `desk/data/whatsapp/` に置き、データ保管の `IGNORE` に足した。

## 決定
- タスクは「1回のやりとりで1件」。こちらが返すと次の取り込みで完了。
- 返事が要らない終わり方の会話は、Claude が `noreply` でアーカイブする。

## 分かったこと
- WhatsApp の相手は `@lid` の形式が多く、電話番号が取れない。そのときは `whatsapp://` でアプリだけ開く。
- メッセージの型番号は公開仕様ではなく、この Mac のデータから読み取ったもの。

## 未確認
- 定期タスクとしての実走（手動実行は権限で止まった）。Gmail の `mail-add`・`mail-replied` は実データで未実行。
- Desk アプリでのコピーと `whatsapp://` の起動。
- デザインの本は全文を読めず、`design-nondesigner.md` のルールで確かめた。

## 次の一手
- 16:00 の実行結果を見る。問題なければ main に入れる。
