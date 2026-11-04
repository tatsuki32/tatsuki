# 2026-11-04 Desk の集中（FocusBlock）

## ひとこと
- いま：集中の画面が main に入った
- 次：本物のブロックを本人が試す
- 待ち：実機での確認

## やったこと
- `focusblock/`：Mac のアプリとWebサイトをブロックする常駐（`daemon.py`）、設定画面（`ui.html`）、解除ダイアログのアプリ（`launcher.applescript`・`focusctl.py`）、導入と削除のスクリプト。
- `desk/server.py`：`GET /api/focus/state`・`POST /api/focus/start|stop` で FocusBlock に中継する。
- `desk/index.html`：「🎯 集中」の画面、Home のカード、解除の入力欄。Mac 版だけに出す。

## 決めたこと
- 解除はパスワードで3か所から：Desk、設定画面、`/Applications/FocusBlock.app`。アプリはブロックされないので、ブラウザが使えなくても解除できる。
- Claude・Claude のサイト・システム設定は設定に関係なく常にブロックしない（`ALWAYS_ALLOWED_APPS`・`ALWAYS_ALLOWED_HOSTS`）。
- Desk はパスワードを保存もログもしない。リストとスケジュールの編集は FocusBlock の設定画面に任せる。
- Life OS とは別に main へ入れた。作業ブランチに Life OS の未公開コミットが混ざっていたため。

## 中断した場所
- 開発用のポートで動かして確かめた。本物のブロックをDeskから始める、ブラウザが実際に止まる、ブロック中に解除ダイアログを出す、は未確認。

## 次の一手
- 本人が短い時間で本物のブロックを試す。違っていれば直す。

## 覚えておくこと
- 許可リスト方式でアプリ0個のままだと、ブラウザも閉じる。先に使うブラウザを足す。
- パスワードの再設定は、常駐を止めてから `--reset-password`。動いたままだと常駐が設定を上書きする。
- `osascript` の `st` は予約語。AppleScript の変数名に使わない。
