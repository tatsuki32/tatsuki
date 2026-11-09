## ひとこと
- いま：PC の画面ができた
- 次：本番でアプリを閉じて試す
- 待ち：サーバーの再起動

# PC 画面（2026-11-05）

- `desk/pc_monitor.py`、`desk/app/appctl.swift`、`lifeos/tests/test_desk_pc.py` を追加。`server.py` に `GET /api/pc`・`POST /api/pc/close` と監視のスレッド。
- 使用メモリはアクティビティモニタと同じ数え方（`vm_stat` の Anonymous − purgeable + wired + compressor）。アプリごとの数字は `ps` の RSS で共有分が重なるので「目安」と表示。
- ヘルパー（`Claude Helper` など）も `lsappinfo` に載るので、外側の `.app` が登録済みならそちらに合算する。登録のない `.app`（claude、Python）は1プロセスずつ扱う。
- AppleScript の `quit` はアプリごとに許可画面が出るので使わない。`NSRunningApplication` は許可が要らない。
- プレビューのサーバーは Claude の子なので、そこから見ると Claude は保護される（祖先を守るため）。launchd のサーバーでは閉じられる。
- Claude のサンドボックスでは `ps`・`sysctl`・localhost への curl が止まる。
- 確かめたこと：ツールの閉じる・5秒後の強制終了、他サイトの Origin が 403、375px で横スクロールなし。未確認：本物のアプリの終了と保存の確認。
