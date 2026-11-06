## ひとこと
- いま：Desk が使い方を数え始めた
- 次：11/23 の最初の並び替えを見る
- 待ち：Desk の窓の再読み込み

# 使い方の計測と週1回の並び替え（2026-11-06）

- 追加：`desk/usage.py`、`lifeos/tests/test_desk_usage.py`（18件）。`server.py` に `POST /api/usage`、`GET /api/usage`、`GET|PUT /api/layout`、ページへの `window.LAYOUT`。`index.html` に計測・メニューとホームの並び・「使い方」の画面。
- 計測は画面を離れるときに1回 `sendBeacon`。60秒ごとと窓が隠れたときにも途中分を送る（`cont:true` は回数を足さない）。
- ボタンの名前：camelCase の id → `data-` の属性名（値は使わない）→ 行き先の画面 → クラス名。`id="${t.id}"` のような動的な id を拾わないよう、大文字を含む id だけ使う。
- ホームのカードは DOM を変えず、各カードの最初のタグに `data-ucard` を差し込む（`uTag`）。カードを足すときは `HOMECARDS` に入れる。
- 並び替えの規則は `plan()`。メニューは最大3つ。ホームのカードは上から順に「そこに来るべきカードを上げる」を3回まで。段は「5回に1回以上押す／押したことがある／押していない」の3つで、同じ段の中の順番は変えない。
- 最初の並びは `home: []` で持つ（「最初の並びに戻す」を出すかどうかの判定に使う）。
- 試験：`.claude/launch.json` の `desk-usage-test`（`DESK_USAGE_DIR=/tmp/claude/desk-usage-test`、ポート 4792）。本物のデータには書かない。
- ハマり：プレビューのスマホ表示は `index.html` に viewport の指定がなく 980px になる。375px は幅375の iframe で確かめた。Claude のサンドボックスから `launchctl kickstart` は止まる。
- 確かめたこと：計測（入口・滞在・カードの押した割合）、2分で止まる、並び替え、お知らせ、元に戻す、固定、自動のオン・オフ、メニューの数が前後で30のまま、375px と 1200px で横スクロールなし。
- 未確認：本物の Desk アプリの窓での計測（再読み込み後）、11/23 の最初の自動の並び替え。
- 全体のテストで2件落ちるが、今回の変更とは別（`test_14_lifemap` の `skipped`、`test_11_desk` の `SCHEDULED_TASKS.md`）。
- 本は Drive で p.136 まで読んだ。後半は `design-nondesigner.md` のまとめで補った。
