# 04 音声メモ整理：ほかのツールとの約束

## 1. 12番（チャット記録）との約束

`logs/expenses.jsonl` に04番も書きます。1行の形は次のとおりで、`item`・`amount`・`currency` は12番と同じです。
12番は `logs/expenses.md` にも書きますが、04番は書きません。

```json
{"at": "2026-11-04T12:00:00+07:00", "item": "ランチ 3万ドン", "text": "ランチ 3万ドン", "amount": 30000, "currency": "VND", "source": "lifeos-04", "memo": "<メモID>"}
```

`currency` は `VND` / `JPY` / `USD`（12番に `USD` はありません）。金額が読めないときは `amount` と `currency` が `null` です。

## 2. 11番（メモ自動連携）との約束

`notes/<メモID>-<番号>-<idea|diary>.md` に、`kind`・`at`・`from`（音声か文字か）の前書きをつけて保存します。

## 3. 定期実行

なし。朝のまとめは `brief.py` なので、01番が動けば入ります。ボットの常駐は土台の `lifeos/bot/run.py` の担当です。
