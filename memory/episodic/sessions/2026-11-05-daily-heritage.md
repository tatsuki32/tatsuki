## ひとこと
いま：毎日世界遺産を作った
次：明日の朝の自動実行を見る
待ち：受ける検定の回を決める

# 毎日世界遺産

- 作ったもの：`desk/heritage.py`、`desk/heritage_countries.json`、`server.py` の `heritage` フィードと `heritagelog`・`/heritage-photos/`、`index.html` の `#heritage` とホームのカード、定期タスク `desk-heritage-daily`、テスト `lifeos/tests/test_desk_heritage.py`。
- 決めたこと：国は199（国連加盟193にバチカン、パレスチナ、コソボ、台湾、クック諸島、ニウエ）。順番は日本から主要な遺産の多い国へ。ただし検定の35件と100件の一覧は手元にないので推定。
- 写真の作者とライセンスは Claude に書かせず、コードが Commons から読む。NC と ND は通さない。
- 今回クイズは入れていない。写真を優先したため。次の版の候補。
- ハマりどころ：Commons の縮小画像は `thumb.wikimedia.org` から来る。短い間隔で呼ぶと 429 を返す。
- 確認：テスト8件が通過。4757番の別サーバーで画面を確認（1200px、375px は iframe で横スクロールなし）。
- 未確認：朝の定期タスクの実走。全部の本文の見直しは本人が読んで行う。デザインの本は p.136 までしか読めない。
