# 2026-11-15 Workout Board

## ひとこと
- いま：運動の動画やメモを貼るボードを足した
- 次：使ってみて直す
- 待ち：なし

## やったこと
- `#workoutboard`（身体のメニュー、Workouts 画面の上に入口）。ボード `wboards`・カード `wcards` は汎用の `GET|PUT /api/<col>/<id>`。
- 動画は `POST /api/uploads` が先頭のバイトで見分けて `data/videos/` に流しながら保存（`video_post`）。`GET /videos/<名前>` は Range 対応（`video_get`）。`backup_data.py` の `IGNORE` に `videos/`。
- 描き直しはカードの差し替えをせず位置だけ更新（`wkSync`）。再生中の YouTube の iframe が 30 秒ごとの読み込みで消えない。

## つまずき
- `.note` と `.wimg` は既存のクラスと重なった。付箋は `.wnc`、画像は `.wbimg`。
- 最初は旧版 `~/Documents/ClaudeTools` に作ってしまった（CLAUDE.md が古いまま「正本」と書いていた）。こちらに移した。
