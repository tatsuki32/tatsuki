# スクリーンタイムの画面

## ひとこと
- いま：アプリ別の時間が見える
- 次：Brave の記録を見る
- 待ち：比較が揃う7日後

## やったこと
- `desk/screentime_sync.py`：knowledgeC.db の `/app/usage` と `/app/webUsage` を日ごとに集計する。
- `GET /api/screentime` と Desk の画面（期間、前の同じ期間との差、サイト、アプリ、時間帯）。
- launchd の `com.tatsuki.desk.screentime`（毎時）。保管からは外した。

## 分かったこと
- launchd から動かす Python はフルディスクアクセスが効かなかった。起動だけする小さなプログラム `TatsukiScreenTime` に許可を出すと読めた。
- `/app/webUsage` は Safari だけ。Chrome と Brave のサイト別は記録されない。

## 残り
- Brave の記録の確認。Chrome は記録できた。
