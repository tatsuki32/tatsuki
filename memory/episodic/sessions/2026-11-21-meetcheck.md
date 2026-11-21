# 2026-11-21 会議の判定「Should we meet?」

## ひとこと
- いま：会議するかを4問で決められる
- 次：Google ドキュメントの許可を押す
- 待ち：本人の Allow と本物の会議で試す

## やったこと
- 要件定義：https://claude.ai/artifact/T4m5SF6jihhKYudXZhrAvU
- `desk/meetcheck.py`：規則（`check`・`verdict`・`cost`）、Claude Haiku の下書き（`draft`・`alternative`、構造化出力）、事前資料の Google ドキュメント（HTML を multipart で上げて Docs に変換 → `permissions` で anyone/commenter）、`drive.file` の OAuth（`docs.py` と同じ PKCE＋ローカルの受け口、refresh は `tatsuki-desk-gdocs`）。
- `desk/server.py`：`GET /api/meetcheck/status`、`POST /api/meetcheck/draft|alt|doc|connect`、コレクション `meetchecks`。
- `desk/index.html`：`#meetcheck`（一覧・今週と合計・時給）、1問ずつのカード（打つたびに通る／止まるを表示）、答え（会議しない・会議する・Meet anyway）、Calendar の予定に「Should we meet?」。

## 確かめたこと
- `python3 -m unittest desk.tests.test_meetcheck`（13件）。
- 偽のサーバー（4783、`MEETCHECK_FAKE=1`）で、予定から開く → 下書き → 5問 → 会議しない（Wins のお祝い）→ タスク化 → Meet anyway → 資料のリンクまで。375px の枠で横スクロールなし。
- 本物の Haiku で下書き2件と代わりの文。日本語の題には日本語で答える。

## ハマりどころ
- `mc` で始まる名前（`mcLoad`・`mcView`・`S.mc`）はメール整理が使っている。新しい画面は `mtg`。
- 作成直後に保存すると一覧の読み込みが飛ばされ、許可の状態が読まれなかった（`loadedAt` で区別）。
- Desk の HTML に viewport の meta がないので、ブラウザのスマホ表示は 980px で描く。375px は iframe で確かめた。
- 作業中に別のセッションが 11-15 の古い stash を当て、`server.py` などに衝突の印が残った。stash の中身だけだと確かめて HEAD に戻した（stash は残してある）。
