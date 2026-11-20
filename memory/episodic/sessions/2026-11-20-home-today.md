# 2026-11-20 ホームを Today 中心にした

## ひとこと
- いま：ホームの先頭を Today にした
- 次：Mac でシートの CSV を取り込む
- 待ち：World Desk を Mac で無効に

## やったこと
- Desk の使い方の見直し（コード・Drive のシート・Drive の文章）を出し、要件定義（Docs）を作って実装した。
- Home：`todayBlock`（予定・期日・ルーティンと習慣・今日のタスク・Sift の見出し）。`topThree` は呼ばない。`decideBar` は `suggCard` を返す。G・右上・More は `openStuck`（`/kickoff`）。`backBar` と Esc で Today に戻る。
- 通知：`notify.py` の 6 時間前をやめた。`tg_notify.py` に `digest`（朝1通）、追加・完了・削除・進行中は既定オフ（`VERSION=2` で古い設定を切り替え）。
- Sift：Worker に `GET /api/desk/today`。`news_sync.py` が `data/news/today.json` に置く。公開（`cd sift && npx wrangler deploy`）が要る。
- 体：`lifelog.mark_log_habits`（記録で習慣に✓）、`bdLimitWord`（上限の栄養は超過量）、`desk/lifelog_sheet.py`（シートを1回取り込む）。
- 使い方：`usage.tryout`・`ask_list`、op `hide`・`keep`・`unhide`。tryout は plan より先に動く（未使用は Rarely used ではなく Not tried yet へ）。
- World Desk：`WORLD_EDITION.md` の先頭に停止の一文。定期タスク自体は Mac の Claude アプリで無効にする。

## 次の一手
- Mac で Life Dashboard の各タブを CSV に保存し、`python3 desk/lifelog_sheet.py <CSV…>` で中身を見てから `--write`。
- Mac の Claude アプリで `desk-world-brief` を無効にする。

## メモ
- 前からあるテストの失敗3件（test_14_lifemap・test_11_desk・test_desk_replywin）は main でも同じ。今回の変更とは別。
