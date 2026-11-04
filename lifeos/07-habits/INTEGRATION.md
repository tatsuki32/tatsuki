# 07 罰しない習慣：統括に頼む変更

## 1. `desk/index.html`：習慣カードに植物を出す

マージのあとで、リポジトリのルートから当てます。

```sh
git apply lifeos/07-habits/desk-plant.patch
```

足すのは `PLANTS`・`plantLine` と、習慣カードの1行だけです。CSS は足しません。赤、責める文言、リセットは入りません。`PLANTS` は `core.py` の `STAGES` と同じ表です（テストで照合）。

コピーの Desk に当て、375px で横スクロールが出ないことと、4段階の表示を確かめました。適用後は `desk/index.html` をオンライン版にも公開します（CLAUDE.md の公開手順）。

## 2. 定期実行：週に1回の報告

`~/Library/LaunchAgents/com.tatsuki.lifeos.07-weekly.plist` を次の内容で置き、`launchctl load` します。日曜 18:00（Mac の時刻）に動きます。Mac が眠っていれば、次に起きたときに1回動きます。同じ週に2回は送りません。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tatsuki.lifeos.07-weekly</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>lifeos/07-habits/weekly.py</string>
  </array>
  <key>WorkingDirectory</key><string>__HOME__/TatsukiDesk</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>0</integer>
    <key>Hour</key><integer>18</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
</dict>
</plist>
```

`__HOME__` はホームフォルダの絶対パスに置き換えます。10番（自動起動）でまとめて管理するなら、そちらに同じコマンドを足すだけで動きます。

## 3. `lifeos/lib/`（急ぎません）

`core.py` は `tasks._server_up`・`tasks._call`・`tasks._locked` を使っています。Desk の API と保存先のロックを二重に作らないためです。共通の公開関数に分けるなら、そちらに差し替えます。
