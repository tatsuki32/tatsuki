# 2026-11-20 Desk の Translate（翻訳と多言語の言葉のつながり）

## ひとこと
- いま：Translate 画面と概念のページを main に入れた
- 次：Google の鍵をキーチェーンに入れて Google で訳す
- 待ち：①「Desk Reader と暗記カード」の Cards 画面

## やったこと
- 要件定義 https://claude.ai/artifact/FF9Tx7iqYRVoLsTo7Nf8kj をそのまま実装。
- `desk/translate.py`：Google Cloud Translation Basic v2（鍵はキーチェーン `tatsuki-desk-gtranslate`）→ 鍵なし・月の上限超えは Claude（haiku）。同じ文はキャッシュ。1語は Claude で原形・gloss・例文・他言語を足し、`concepts` につなぐ（候補の id だけ使う）。Merge・Split。2回調べた語は To cards の候補、Add で `cards`（①の形）。Web履歴の Google 翻訳のページと「〜とは」検索の取り込み（`translate.watch`、1時間に1回、夜に概念）。
- `desk/server.py`：`lookups`・`concepts`・`cards` を COLLECTIONS に、`/api/translate*`・`/api/concepts/merge|split`、`translate.bind`・`translate.watch`。
- `desk/chrome_web_sync.py`：`KEEP_PARAMS` に translate.google.com の `text`・`sl`・`tl`。
- `desk/index.html`：Translate 画面（入力欄・訳す先のチップ・結果・概念の帯・History／Words／To cards・Settings）、`#concept/<id>`、検索欄の `>`、選択＋T キー、選択メニューの Translate、`/` 一覧、`FDESC`。部品のクラス名は `xl`。
- 試験：`desk/tests/test_translate.py`（11件）。ブラウザで Claude 経由の訳・概念の帯・`> 用`・Merge・T キーを確認。

## 気づき
- Hiragino Sans のキリル文字は字間が広く見える。`[lang]` が ja・zh・ko 以外のときだけ system-ui にした。
- Claude の候補が狭すぎると同じ意味の概念が二重にできる（使う と 用）。候補は「同じ語 → 近い語 → 新しい順」で10件にした。
- 同じフォルダで別の Claude セッション（Reader と暗記カード、Forms）が同時に index.html と server.py を直していた。コミットは HEAD に自分の差分だけを当て直して作った。

## 残り
- Google Cloud の鍵と請求先は本人が作る（それまで Claude が訳す）。無料枠の数字は公式の料金表で確かめる（このセッションでは読めなかった）。
- 本番の 4747 でのサーバー再起動後に、夜の取り込みと概念づけの実走を確認。
- 次フェーズ（iPhone、他アプリのショートカット、写真、週のまとめ、ノート連携）。
