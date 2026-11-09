## ひとこと
- いま：Web履歴の画面が動いた
- 次：Chrome に拡張を入れる
- 待ち：拡張の読み込み

# Web履歴（2026-11-05）

- `desk/chrome_web_sync.py`：Chrome の `History` のコピーから訪問と検索語を SQLite に貯める。launchd で毎時。
- `desk/chrome-extension/`：前面のタブの時間を測り、`POST /api/web/time` に1分ごとに送る。ID は manifest の `key` で固定。秘密鍵は持たない。
- Chrome の `visit_duration` は開きっぱなしのタブも数えるので使わない。滞在の推定は「次の訪問までの間隔と記録の短い方、上限5分」。
- 実測のある日は実測だけ、ない日は推定だけ。実測は同じページの直近の訪問に割り当てる。
- サンドボックスでは `.claude/launch.json` を書けず、ポートも開けない。画面の確認は作り物のデータのサーバーをサンドボックス外で動かした。

未確認：本物の Chrome での拡張の動作（Chrome の API を真似た試験だけ）。Default のみが対象。
