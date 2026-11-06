# 04 音声メモ整理

## 目的

思いついたことを声か文字で送るだけで、種類ごとの置き場に入れます。
送ってから1分以内に、どこへ入れたかを返事します。翌朝には前の日のまとめを10行まで出します。

## 使い方

Telegram のボットに送ります。ほかのツールが受けなかったものを、すべてこのツールが受けます（`/` で始まるものは除く）。

| 種類 | 入る場所 |
| --- | --- |
| タスク | Desk のタスク（`source` は `lifeos-04`）。日時が言葉にあれば期日も入る |
| 予定 | Desk のタスク（期日つき、`note` に `種類:予定`）。カレンダーには入れません |
| 支出 | `logs/expenses.jsonl`（金額と通貨つき） |
| アイデア・日記 | `notes/<ID>-<番号>-<種類>.md` |

- 届いた文面はまず `inbox/` に保存し、すべて仕分けたら消します。保存に失敗した分は `inbox/` に残ります。
- 1通は文ごとに判定します。手がかりのない文は、前の文につなげます。
- 全体に手がかりがなければ、アイデアに置いて「迷いました」と返します。
- 先頭に `タスク：` `アイデア：` `日記：` `支出：` `予定：` と書くと、1通すべてがその種類になります。
- 仕分けはキーワードの規則です。AI は使わないので費用はかかりませんが、間違えることはあります。間違えた分は Desk やファイルで直します。

```sh
python3 lifeos/bot/cli.py "明日15時に歯医者"          # Telegram なしで試す（本物に入る）
LIFEOS_DATA="$TMPDIR/try" python3 lifeos/bot/cli.py "牛乳を買う"   # 試し用の場所に入る
python3 lifeos/04-voice/voice_memo.py yesterday      # 昨日のまとめを出す
python3 lifeos/04-voice/voice_memo.py sweep          # inbox/ に残った分をもう一度仕分ける
```

朝のまとめは `brief.py` が出し、01番が `tools.brief_parts()` で集めます。01番を使わなくても `yesterday` で読めます。

## つなぐもの

- Telegram：設定済みです。
- 文字起こし：Groq の Whisper（`whisper-large-v3-turbo`、日本語指定）を使います。声は Groq に送られます。
  キーはキーチェーンに入れます。ボットを起動し直さなくても、入れた次の音声から動きます。

```sh
security add-generic-password -a groq -s tatsuki-desk-groq -w
```

- モデルを変えるときは、データ置き場の `tools/04-voice/settings.json` に `{"model": "whisper-large-v3"}` と書きます。
- 別の方法にするときは、音声ファイルのパスを受け取って文字を返す関数を作り、環境変数 `LIFEOS_TRANSCRIBER="パッケージ.モジュール:関数"` で指定します。
- 回数の上限を超えたときや Groq が止まっているときは、音声を `inbox/audio/` に残して返事します。あとで `python3 lifeos/04-voice/voice_memo.py audio` でやり直せます。

## 止め方

`tool.json` の `status` を `stopped` にして、ボットを起動し直します。ボットはこのツールを読み込まなくなり、朝のまとめにも出なくなります。
止めている間、届いた文は `logs/unrouted.jsonl` に残ります。保存済みのメモとタスクはそのままです。
