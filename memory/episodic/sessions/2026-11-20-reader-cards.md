# 2026-11-20 Desk Reader と暗記カード

## ひとこと
- いま：本を読んで単語をカードにし、復習できる画面を作った
- 次：iPhone のアプリを公開（cards/setup.sh）、シートと Anki を取り込む
- 待ち：Cloudflare の公開と合言葉の登録は本人の操作

## やったこと
- `desk/reader.py`：本の保管（`desk/data/reader/books/`）、貼り付け・.txt・.epub・PDF（`desk/app/pdftext.swift`）の読み込み、言語推定、続きの位置、`say` での読み上げ。
- `desk/cards.py`：カードの追加（同じ言葉は1枚）、Claude Haiku での意味・発音・例文・原形（キャッシュ `dict.json`）、30分ごとの埋め直し、シートと Anki の取り込み、今日の枚数。
- `desk/cards_sync.py`＋`cards/`：iPhone 用の復習アプリ（Pages＋KV、Routine と同じ作り）。
- `desk/index.html`：Reader（一覧・読む画面・読み上げの帯・言葉のカード）、Cards（一覧・編集・候補）、復習（FSRS-4.5、キー 1〜4・Space・Z）、Today の「Cards due」。
- テスト：`desk/tests/test_cards.py`・`test_reader.py`（20件）。ブラウザで 375px の横スクロールなしを確認。

## 決めたこと
- iPhone の同期は要件の D1 ではなく Routine と同じ Pages＋KV（既に動いている形。カードは数千枚でも 1 値に収まる）。
- 同期の衝突はカードごとに `updatedAt` の新しい方。復習の記録は足し合わせる（FSRS の再計算はしない）。
- Translate（別セッション）が同じ `cards` コレクションに書くので、`fsrs.state` のないカードも受ける。

## 気づき
- 同じ Mac で複数の Claude セッションが `index.html`・`server.py` を同時に直していた。共有ファイルは HEAD に自分の差分だけを当てた版を index に入れてコミットした（他のセッションの未コミット分を main に混ぜないため）。
