# 04 音声メモ整理：統括への依頼

## 1. `lifeos/bot/run.py`：文字起こしの失敗で音声を失わないようにする

今は `voice.transcribe` が例外を出すと、`poll_once` がログに残すだけで、本人に返事が届かず、音声も一時フォルダごと消えます。
文字起こしをつなぐ前に入れてください。全テストが通ることを、作業ツリーのコピーで確かめました。

先頭の import に `import shutil` を足し、`handle_update` の次の行を置き換えます。

```python
            text = voice.transcribe(os.path.join(d, "voice"))
```

```python
            try:
                text = voice.transcribe(os.path.join(d, "voice"))
            except Exception as e:
                logs.append("bot-errors", {"where": "transcribe", "error": type(e).__name__})
                shutil.copy(os.path.join(d, "voice"), paths.data_path("inbox", "audio", str(update.get("update_id"))))
                client.send_message(chat, "文字起こしに失敗しました。音声は inbox/audio/ に残しました。")
                return
```

## 2. 12番（チャット記録）との約束

`logs/expenses.jsonl` に04番も書きます。1行の形は次のとおりです。12番が別の形にするなら、どちらかに合わせます。

```json
{"at": "2026-11-04T12:00:00+07:00", "text": "ランチ 3万ドン", "amount": 30000, "currency": "VND", "source": "lifeos-04", "memo": "<メモID>"}
```

`currency` は `VND` / `JPY` / `USD`。金額が読めないときは `amount` と `currency` が `null` です。

## 3. 11番（メモ自動連携）との約束

`notes/<メモID>-<番号>-<idea|diary>.md` に、`kind`・`at`・`from`（音声か文字か）の前書きをつけて保存します。

## 4. 定期実行

なし。朝のまとめは `brief.py` なので、01番が動けば入ります。ボットの常駐は土台の `lifeos/bot/run.py` の担当です。
