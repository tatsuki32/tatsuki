# 04 音声メモ整理

## 目的

思いついたことを声か文字で送るだけで、やることと予定に分けて入れます。
翌朝には前の日のまとめを10行まで出します。

## 使い方

Telegram のボットに送ります。ほかのツールが受けなかったものを、このツールが受けます（`/` で始まるものは除く）。
音声なら、まず文字起こしの全文が届き、続けて仕分けた結果が届きます。

| 種類 | 入る場所 |
| --- | --- |
| 日時のあるタスク・予定 | Google カレンダー。時刻がなければ終日の予定 |
| 日時のないタスク | Desk のタスク（`source` は `lifeos-04`） |
| 支出 | `logs/expenses.jsonl`（金額と通貨つき） |
| アイデア・日記 | `notes/<ID>-<番号>-<種類>.md` |

- 文字起こしと仕分けは Groq が行います。声も文字も Groq に送られます。
- Groq が使えないときは、簡単な規則で仕分けて、そう返事します。
- 届いた文面はまず `inbox/` に保存し、すべて仕分けたら消します。保存に失敗した分は `inbox/` に残ります。
- 先頭に `タスク：` `アイデア：` `日記：` `支出：` `予定：` と書くと、AI を通さず1通すべてがその種類になります。

```sh
python3 lifeos/bot/cli.py "明日15時に歯医者"          # Telegram なしで試す（本物に入る）
LIFEOS_DATA="$TMPDIR/try" python3 lifeos/bot/cli.py "牛乳を買う"   # 試し用の場所に入る（キーがないので規則で仕分ける）
python3 lifeos/04-voice/voice_memo.py yesterday      # 昨日のまとめを出す
python3 lifeos/04-voice/voice_memo.py sweep          # inbox/ に残った分をもう一度仕分ける
python3 lifeos/04-voice/voice_memo.py audio          # 文字起こしに失敗した音声をやり直す
```

朝のまとめは `brief.py` が出し、01番が `tools.brief_parts()` で集めます。01番を使わなくても `yesterday` で読めます。

## カレンダーをつなぐ

つなぐまでは、日時つきの話は期日つきの Desk タスクに入ります。カレンダーに書けなかったときも同じです。

1. [Apps Script](https://script.google.com/) で新しいプロジェクトを作り、`calendar-webhook.gs` の中身を貼る
2. 「デプロイ」→「新しいデプロイ」→ 種類は「ウェブアプリ」。実行は自分、アクセスは「全員」にする
3. Google の許可画面で許可し、出てきた `/exec` の URL をコピーする
4. ターミナルで次を実行して URL を貼る。URL はパスワードと同じ扱いにする

```sh
security add-generic-password -a webhook -s tatsuki-desk-gcal -w
```

入るのは既定のカレンダーです。予定を足すだけで、読むことも消すこともできません。

予定の長さは15分です。話に長さや終わりの時刻があれば、その長さにします。

`calendar-webhook.gs` を変えたら、Apps Script に貼り直して「デプロイ」→「デプロイを管理」→ 鉛筆 → バージョン「新バージョン」→「デプロイ」とします。URL は変わりません。

## つなぐもの

- Telegram：設定済みです。
- Groq：キーはキーチェーンに入れます（`security add-generic-password -a groq -s tatsuki-desk-groq -w`）。
- モデルを変えるときは、データ置き場の `tools/04-voice/settings.json` に `{"model": "whisper-large-v3", "analysis_model": "openai/gpt-oss-20b"}` のように書きます。
- 回数の上限を超えたときや Groq が止まっているときは、音声を `inbox/audio/` に残して返事します。

## 止め方

`tool.json` の `status` を `stopped` にして、ボットを起動し直します。ボットはこのツールを読み込まなくなり、朝のまとめにも出なくなります。
止めている間、届いた文は `logs/unrouted.jsonl` に残ります。保存済みのメモとタスクはそのままです。
