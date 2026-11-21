# 試験が本物のデータを消さないように

2026-11-21、`python3 -m unittest desk.tests.test_minutes_share desk.tests.test_minutes …` を一緒に走らせたとき、本物の `desk/data/minutes/` が消えた。

## なぜ起きたか
- `minutes.DIR` は import したときに環境変数 `MINUTES_DIR` を読んで決まる。
- `test_minutes_share` が先に `minutes` を import した（`MINUTES_DIR` はまだない → 本物のフォルダ）。
- あとの `test_minutes` が `MINUTES_DIR` を設定しても、モジュールはもう読み込み済み。`setUp` の `shutil.rmtree(minutes.DIR)` が本物を消した。
- `minutes/` は保管（`backup_data.py` の `IGNORE`）の外で、Time Machine もないので戻せなかった。Transcribe の録音とメモから作り直した。

## 守ること
- 置き場を消す・書く試験は、消す前に「試験用のフォルダか」を確かめる（`test_minutes.py` の `setUp` の形）。
- 置き場を環境変数で決めるモジュールを import する試験は、import の前に自分で一時フォルダを設定する（`os.environ.setdefault(...)`）。
- 試験を何本か一緒に走らせるときは、import の順で置き場が変わることを疑う。
