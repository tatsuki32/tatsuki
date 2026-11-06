## ひとこと
- いま：小さな道具を4つ作った
- 次：マージしてアプリを作り直す
- 待ち：窓での保存の確認

# 小さな道具：QR・文字数・URL短縮・PDF圧縮（2026-11-06）

- 追加：`desk/utilities.py`、`desk/app/pdfshrink.swift`、`lifeos/tests/test_desk_utilities.py`。`server.py` に `utilities_post`・`utility_file`。`index.html` に `utView` のまとまり。
- PDF：`gs` と `qpdf` はないが、`PDFDocument.write` に `QuartzFilter` を渡すと文字を残して画像だけ再圧縮できる（キーは文字列 `"QuartzFilter"`）。書いたあとにページ数・文字数・注釈数を比べ、変わったら捨てる。
- QR：外部ライブラリなしで Python で作った。40の版すべてを macOS の `CIDetector` で読み戻して確認した。
- 保存：launchd のサーバーから `~/Downloads` に書くと macOS の保護にかかるおそれがあるので、`/api/wise/export` と同じくブラウザのダウンロードに任せた。そのため「開く」「Finder で表示」のボタンはない。
- WKWebView はデリゲートがないと `<input type=file>` の窓を出さない。`main.swift` に `runOpenPanelWith` を足した（ノートの「ファイルを選ぶ」にも効く）。
- 確認は別ポート。サンドボックス内は待ち受けができないので外で動かし、`lifemap_ticks` を起動しないよう `server.Handler` だけを立てた。
