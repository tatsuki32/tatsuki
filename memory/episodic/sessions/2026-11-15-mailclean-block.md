# 2026-11-15 メール整理：手でブロック

## ひとこと
- いま：手でブロックを足した
- 次：7日で切れる接続を直す
- 待ち：Google の本番切り替え

## やったこと
- Gmail の接続切れを直した。原因は OAuth アプリが「テスト中」で、refresh token が7日で切れること。`gmail_clean.py auth` はキーチェーンの client を使い回せるようにした（JSON の再ダウンロードは不要）。
- 「Block a sender」：アドレスかドメインを入れると、Stop と同じフィルター（受信トレイを飛ばしてゴミ箱）を作る。`POST /api/mailclean/block`、解除は undo。記録は `decisions.json` の `m-` キー。共用ドメインと自分のアドレスは断る。`PROTECT` に当たると 409 で確認。
- 本物の Gmail で、登録・二重登録・確認・解除を確かめた。

## ハマりどころ
- `LOCK` は再入できない。`with LOCK:` の中で `view()` を呼ぶと止まる。
- 別セッションが同じファイルを触っているときは、`GIT_INDEX_FILE` の一時インデックスに自分の hunk だけを `git apply --cached` してコミットする。隣の行の変更が hunk に混ざらないか、コミット後に確かめる。

## 次の一手
- Google Cloud Console で OAuth アプリを「本番」にする（Tatsuki）。
