# 2026-11-14 タスクを Google カレンダーに入れるボタン

## ひとこと
- いま：タスクに「カレンダーへ」ボタン
- 次：本番で1件入れてみる
- 待ち：スマホで説明欄を確認

## やったこと
- 全部のタスクを同期する案はやめた。カレンダーが埋もれるため。やる日時を決めたものだけ、本人がボタンで入れる。
- `desk/index.html`：`wireCal`（タスク詳細のパネル）、`calDetails`・`calUrl`・`calStart`・`calLen`・`tzName`。Google の作成画面（`action=TEMPLATE`、`ctz` に Mac の時間帯）を開くだけ。キーも OAuth も使わない。
- 開いた記録は `calAt`・`calMin`・`calTz`。保存を押したかは Desk から分からない。
- `#task/<id>` で詳細が開く（説明欄の最後のリンク）。
- 「今日の3つ」の Block time も同じ説明欄にした。
- 定期タスク `desk-task-calendar` を停止（コード・対応表・入った予定は残した）。

## 気づき
- Desk には viewport の meta がないので、375px の確認はブラウザの模擬では幅が変わらない。
- Desk は日付の計算をホーチミン（+7）で固定している。このボタンだけ Mac の時間帯に従う。ジョージアに移ったら全体を直す必要がある。

## 要件定義
https://claude.ai/artifact/LNBRdPyuUn1WYimPi7rTHw
