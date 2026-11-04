# 14 人生マップ：共有ファイルの変更

共有ファイルの次の変更は、このブランチに含めています。

- `desk/server.py`：`lifemap` の API と、15分ごとに `core.tick()` を動かす処理。常駐しているサーバーが動かすので、定期実行を別に登録しません。
- `desk/index.html`：Life OS 画面の「人生マップ」に、今日の問い・答え・月の気づきを出す。
- `desk/sync.py`・`desk/sync/HOURLY_SYNC.md`：`lifemap` を双方向で同期する。

サーバーの変更は、Desk サーバーの再起動で有効になります。
