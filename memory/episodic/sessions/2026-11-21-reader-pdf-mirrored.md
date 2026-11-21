# 2026-11-21 Reader：逆さまになる PDF を直した

## ひとこと
- いま：英語の本の PDF が正しく読める
- 次：別の本でも試す
- 待ち：なし

## 何が起きていたか
- Blinkist の要約を calibre → Chrome（Skia/PDF）で PDF にしたものを Reader に入れると、全ページの文字が逆順（「tsiknilB yb edaM」）になり、言語も it と判定された。
- 原因は macOS の PDFKit。この形の PDF では文字の位置を点対称（x も y もページの外のマイナス）に計算し、読み順を右下から左上にしてしまう。見た目の PDF は正常。

## 直したこと
- `desk/app/pdftext.swift`：文字の多くがページの外・左下に出るページは文字列を逆にし、先頭に `\x01` を付ける。
- `desk/reader.py`：`\x01` のページだけ `fix_mirrored`。逆にすると合字（fi・fl・ffi）が「if」になるので、Mac の単語リスト（`/usr/share/dict/words`、語尾 s・ing などを外して照合）で直す。字間の詰まった行で消えた空白も、12字以上の単語でない並びだけ分ける。
- `chapters_from_pdf`：白紙ページの次のページが大文字で始まる短い段落なら、その段落を章の題にする。
- 取り込み済みの2冊（同じ本を2回入れたもの）は同じ id のまま作り直した（位置は先頭に戻る）。

## 残り
- 空白の分け方は単語数が同じ候補で迷うことがある（例 "me aside as" が "me as ideas" になる1か所）。
- `test_voice_parsing_and_filter` は Kyoko (Enhanced) の並びで前から失敗している（今回とは無関係）。
