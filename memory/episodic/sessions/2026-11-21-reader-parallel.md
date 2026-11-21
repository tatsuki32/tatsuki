# 2026-11-21 Reader の対訳モード

## ひとこと
- いま：本を訳と並べて読めるようにした
- 次：本物の本で1章試す

## やったこと
- `/desk-feature` で質問 → 要件定義 → 承認（音声で「元の言語で作って、日本語と英語のとき訳して」）。
- `desk/reader_parallel.py`：章の対訳を Claude Haiku に頼み、位置はコードが本文から探して `reader/parallel/<本>/<章>.json` に保存。試験 `desk/tests/test_reader_parallel.py`（8件）。
- `desk/server.py`：`GET|POST /api/reader/books/<id>/parallel/<章>`。
- `desk/index.html`：Read / Parallel の切り替え、段落ごとの左右、押した語の組を両側で光らせる、Translated の印と一覧（Add to Anki・×）、読み上げ中の訳の文も光る。
- 試験用サーバー（4786）で、公有の英文を本物の Claude で1章訳して確認：約20秒、イディオムは「in possession of = を持つ」「gave up arguing = 議論をあきらめ」のように1組。再読み込みでは送らない。375px で横スクロールなし。

## 決めたこと
- 訳す先は日本語。本が日本語なら英語（本人の指示）。
- Claude には位置を出させず、文字列を返させてコードが探す（作り話の組が画面に出ない）。
- 印は Translated の1種類だけ（本人の選択）。Add to Anki は今の `cards` に送り、意味は今までどおり Claude の辞書で埋まる。
- Parallel では選択の帯と今の訳カードを出さない（押すと組を光らせるだけ）。

## 中断した場所
- なし。main に入れた。
- 別件：`desk.tests.test_reader` の声の並びの試験が main でも1件落ちる（Kyoko (Enhanced) が先頭に来る。声の作業の変更と試験がずれている）。

## 次の一手
- Mac の Desk で本物の本を1章 Parallel で訳し、組の細かさと1章の時間・費用を確かめる。
