## ひとこと
- いま：起動時に古いアプリを閉じる機能を作った
- 次：Mac で setup を1回走らせて再起動で確かめる
- 待ち：Chrome の「前回の続き」設定の確認

# 2026-11-20 Startup Clean

- 要件定義：https://claude.ai/artifact/RAWFz5zmp2rANoiufTGf91 （承認済み）
- `desk/startup_clean.py`：launchd `com.tatsuki.desk.startclean`（RunAtLoad）。起動から1時間以内・1回の起動に1回だけ。40秒後と90秒後に `TatsukiAppCtl list` で Dock のアプリを見て、keep 以外を `quit`。12秒待って消えたものだけを記録。
- 判断：ふた・スリープでは動かさない（本人の選択）。強制終了しない。ログイン後20秒より後に開いて前面にあるアプリ、2回目は75秒より後に開いたアプリは閉じない（本人がすぐ開いたものを守る）。
- `setup-mac.sh` は bootstrap の前に `mark` を呼ぶ。そうしないと入れた瞬間（RunAtLoad）に閉じてしまう。
- LINE は閉じても `linewatch.swift` が自分で開くので問題ない。
- Desk の窓には viewport の meta がないので、ブラウザの 375px 模擬は 980px で描く（前からのこと）。
- 未確認：本物の再起動、通知の表示。
