# 02 返信催促

## 目的

今日の予定、こちらが返すもの、相手の返信待ちを毎朝1か所にまとめます。
返信待ちは3日で催促の下書きを作り、14日で「送る／保留／捨てる」を Telegram で聞きます。
**メールは送りません。** 下書きは Gmail の下書きに入るだけです。

## 使い方

| 読むもの | 元 |
| --- | --- |
| 今日の予定 | Desk の予定（`calendar.js`） |
| こちらが返すもの | Desk の Morning Mail（`mail.js`）。分類は作り直していません |
| 相手の返信待ち | Gmail の送信済み（`MORNING_RUN.md` の手順で読む） |
| SNS の DM | 未接続。`connect.py` の `read_dms` を差し替える |

Python から Gmail には入れないので、Gmail コネクタのある Claude が `MORNING_RUN.md` を実行します。
ブリーフィングには `brief.py` から入ります。01番の変更は要りません。

Telegram や `lifeos/bot/cli.py` で次のように答えます。

```
催促 送る 3     下書きを開いて自分で送るタスクを作る（送信はしない）
催促 保留 3     7日後にまた聞く
催促 捨てる 3   催促をやめる（下書きは消さない）
催促 戻す 3     返信待ちに戻す
催促 一覧       返信待ちを番号つきで出す
```

見本で試す（本物のメールやデータに触りません）：

```sh
python3 lifeos/02-followup/run.py demo
```

## 決まりごと

- 相手から返信が来たら、そのスレッドは閉じます。
- こちらがまた送ると、数え直します。
- 宛先が `noreply` などのものは数えません。
- 自分の短いお礼で終わったスレッドも返信待ちに見えます。不要なら「捨てる」で止めます。
- 下書きは1回の朝に5件まで、問いは3件までです。始めた日に一度に増えないようにしています。
- 問いは下書きができてから出します。
- 設定は `desk/data/lifeos/tools/02-followup/config.json` で変えられます（`draftDays`、`askDays`、`holdDays`、`ignore` など）。

## 止め方

```sh
python3 lifeos/02-followup/run.py stop    # 朝の処理とブリーフィングへの追加を止める
python3 lifeos/02-followup/run.py start   # 再開
```

毎朝の実行も止めるときは、登録したスケジュールタスクを止めます。ほかのツールには影響しません。

## 差し替える場所

外とつながる関数は `connect.py` だけにあります。

| 関数 | 今 | 差し替える例 |
| --- | --- | --- |
| `read_waiting` | Claude が保存したスナップショット | Gmail API を直接読む |
| `read_calendar` / `read_needs_reply` | Desk の同期データ | 別の取り込み |
| `read_dms` | 空 | 決まった SNS の DM |
| `make_draft` | `None`（Claude に任せる） | 下書きを直接作る |
| `ask` | `lifeos.bot.telegram.notify` | 別の通知 |

送信の関数は置きません。テストがコードに送信の呼び出しがないことを確かめています。
