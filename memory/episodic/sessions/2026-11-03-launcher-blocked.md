## ひとこと
- いま：オンライン版が開けない件を直した
- 次：Mac で入口から開いて確かめる
- 待ち：main へのマージ

## 何があったか
- 「claude.ai is blocked / ERR_BLOCKED_BY_RESPONSE」が出た。
- 原因：claude.ai は他のページの枠（iframe・アプリのプレビュー画面）の中に表示されるのを拒む。
  入口 `desk/launcher.html` を枠の中で開いた状態で「オンライン版を開く」を押すと、枠の中で claude.ai を開こうとして止まる。
- 直し方：そのリンクに `target="_blank" rel="noopener"` を付け、必ず新しいタブで開くようにした。

## 覚えておくこと
- claude.ai のページは iframe に入れられない。Desk から claude.ai へのリンクは新しいタブで開く。
