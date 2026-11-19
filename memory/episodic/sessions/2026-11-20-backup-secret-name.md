# 2026-11-20 データの保管が止まっていた件

## ひとこと
- いま：保管の止まりを直した
- 次：Mac で保管の再開を確かめる
- 待ち：とくになし

## 何があったか
- `desk/backup_data.py` が 11/15 から毎回「looks like a secret」で止まり、`desk/data` の非公開の保管が5日間送られていなかった。
- 引っかかったのは `tany/site/wrangler.jsonc` の説明コメント `// Secret: EXPORT_KEY ...`。値ではなくキーの名前。
- 確かめ方：ログは `~/Library/Logs/tatsuki-desk-backup.log`。値を出さずに名前か値かだけ見る1行の python で確認した。

## 直したこと
- `looks_secret()` を足し、`secret: SOME_NAME` のように値が「大文字＋アンダースコアの名前」のときだけ見逃す。アンダースコアのない大文字の並び（本物の鍵のことがある）は今までどおり止める。
- 試した例：名前のコメントは通る。`token: "abcd..."`・`api_key=ABCD...`・`TOKEN = "A1B2..."`・`sk-ant-...` は止まる。

## 次の一手
- Mac が main を取り込んだあと、ログに `saved N files` と `sent` が出るのを見る。
