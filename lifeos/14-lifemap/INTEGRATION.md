# 14 人生マップ：統括への依頼

共有ファイルの変更はありません。頼みたいのは定期実行だけです。

## 15分ごとの定期実行を入れる（必須）

問いかけと月1回の気づきは、`lifemap.py tick` を15分ごとに動かして出します。ボット本体（`run.py`）は変えません。
時刻の抽選、1日1問の判定、夜に出さない判定は `tick` の中にあるので、何度動かしても問いは増えません。

マージのあと、`~/Library/LaunchAgents/com.tatsuki.lifeos14.plist` を次の内容で作ります。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tatsuki.lifeos14</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>cd "$HOME/TatsukiDesk" &amp;&amp; /usr/bin/python3 lifeos/14-lifemap/lifemap.py tick</string>
  </array>
  <key>StartInterval</key><integer>900</integer>
</dict>
</plist>
```

入れるときと外すときは次のとおりです。

```sh
launchctl load ~/Library/LaunchAgents/com.tatsuki.lifeos14.plist
launchctl unload ~/Library/LaunchAgents/com.tatsuki.lifeos14.plist
```

- Telegram のトークンより先に入れないでください。トークンがない間は、問いが `logs/outbox.jsonl` に貯まるだけです。
- 間隔を変えるときは `core.py` の `TICK_MINUTES` も合わせます。

## ボットの常駐

答えを受け取るには `lifeos/bot/run.py` が動いている必要があります。常駐の登録は 14番だけの話ではないので、統括の判断に任せます。
