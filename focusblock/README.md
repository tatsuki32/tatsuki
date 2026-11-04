# FocusBlock

Freedom のような、時間を決めてアプリや Web サイトをブロックする Mac 用ツールです。

## インストール

ターミナルでこのフォルダに移動して、次を実行します（Mac のログインパスワードを聞かれます）。

```bash
./install.sh
```

終わると設定画面（http://127.0.0.1:47800）が開きます。あとからは `/Applications/FocusBlock.app` から開けます。

## できること

- **今すぐブロック**：25分〜4時間のボタン、分数、終了時刻のどれかで指定
- **スケジュール**：曜日と時間帯を決めて自動でブロック（夜をまたぐ時間帯も OK）
- **ブロック対象**：セッションやスケジュールごとに「アプリ」「Webサイト」「両方」から選べる
- **アプリ**：「指定したアプリをブロック」か「指定したアプリ以外をブロック」を選べる。ブロック中に起動すると、すぐ終了して通知を出す
- **Webサイト**：「指定したサイトをブロック」か「指定したサイト以外をブロック」を選べる。Mac のプロキシ設定を使うので Safari / Chrome / Arc / Edge / Brave / Firefox などすべてのブラウザで効く
- **解除**：パスワードを入れれば途中で止められる。ブロック中の設定変更にもパスワードが必要
- Mac を再起動しても、ブロックはそのまま続く

## 常にブロックされないもの

- アプリ：Claude、FocusBlock、Finder、システム設定
- サイト：claude.ai、claude.com、anthropic.com、claudeusercontent.com（サブドメインも含む）

サイトを開くブラウザ自体をブロックしていると、そのブラウザは終了されます。

## 注意

- ブロック中に HTTPS のサイトを開くと、ブラウザ側に「接続できません」というエラーが出ます（これが正常です）。
- ブロックの開始前から開いていたタブは、ページを再読み込みするまで表示されたままになることがあります。
- 「指定したアプリ以外をブロック」では、許可していないアプリが強制終了されます。保存していない作業は失われます。

## ブロックを解除する

1. アプリケーションフォルダ（または Spotlight）から **FocusBlock** を開く
2. 「パスワードで解除」を押して、解除パスワードを入れる

FocusBlock アプリはブロックされないので、ブラウザが使えないときもこの方法で解除できます。

## パスワードを忘れたとき・アンインストール

Mac のターミナルアプリで、次の1行を実行します（Mac のログインパスワードを聞かれます）。パスワードと実行中のブロックが消え、次に設定画面を開くとパスワードを決め直せます。

```bash
sudo launchctl bootout system /Library/LaunchDaemons/com.focusblock.daemon.plist; sudo /usr/bin/python3 "/Library/Application Support/FocusBlock/daemon.py" --reset-password; sudo launchctl bootstrap system /Library/LaunchDaemons/com.focusblock.daemon.plist
```

アンインストールするときは次を実行します。

```bash
sudo "/Library/Application Support/FocusBlock/uninstall.sh"
```

ログは `/Library/Logs/FocusBlock.log` に書き出されます。
