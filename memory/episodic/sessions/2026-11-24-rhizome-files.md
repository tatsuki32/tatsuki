# 2026-11-24 Rhizome Box のファイルと容量メーター

## ひとこと
- いま：手元で全部動いた。main に入れた
- 次：本番のサイトに出す
- 待ち：公開の OK

## やったこと
- 要件定義（https://claude.ai/artifact/LwmXbeKMNe7CnjJ2czmDHd ）→ 本人の「進めて」で実装。
- サーバー：分割アップロード（16 MB ずつ、R2 multipart）、上限（1個 500 MB・合計 9 GB。1文の INSERT…SELECT で確かめて場所を取る）、一覧・削除・Recount、Range、種類は拡張子だけで決める。
- 画面：Add file、進み具合と Cancel・Try again、PDF・動画・音声をその場で表示、Storage のメーター。
- Desk：ツール画面の Rhizome Box のタイルに使用量、写しは流しながら保存、`rhizomebox/files/` を GitHub の保管から外す。
- 自動の安全確認の指摘2件を直した：訪問者に出すファイルは表示と同じ規則で求める、位置情報を消せない写真は確認を出す（`picture.js`）。
- 2回目の指摘3件も直した：消すものを選ぶのをやめ、表示に要る部分だけで組み直す。終わりの後ろの写真や動画も落ちる。RAW・TIFF・知らない形式は確認を出す。
- 試験は31件（`cd rhizomebox && npm test`）。手元のブラウザで 375px と 1200px、120 MB、取り消し、削除、訪問者の表示、位置情報の消去を確かめた。

## 決めたこと
- 4つの答え：何でも入る／9 GB で止める／PDF は最初から中身／メーターは両方。
- 公開ページの写真から場所が分からないよう、写真は向きの情報だけ残して組み直す。

## 分かったこと
- `/files/` の非公開の守りは、前は画像の列だけを見ていた。今はページの行から `filesOf` で見る。
- PDF の枠は DOM を作り直すと読み直しになる。変わった行だけ替える `setBody` にした。
- `.claude/launch.json` は Bash のサンドボックスから書けない。Edit なら書ける。
- 長い行を含む Python を標準入力で渡すと文字コードのエラーになる。ファイルに書いて渡す。

## 次の一手
- `cd rhizomebox && npx wrangler d1 execute rhizomebox --remote --file schema.sql` → `npx wrangler deploy` → 本番で小さいファイルを1つ → Desk のサーバーを再起動。
