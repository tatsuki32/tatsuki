# 2026-11-04 Life OS の土台

## ひとこと
- いま：18個のツールの土台ができた
- 次：ツールごとに作っていく
- 待ち：Telegram のボットを作る

## やったこと
- `lifeos/README.md`：各ツール担当の共通の約束。編集してよい場所、タスクとボットの呼び方、テストの決まり。
- `lifeos/NN-slug/tool.json`：18個を `planned` で置いた。仕様書の社名・案件名は入れていない。
- Desk の「Life OS」ビュー：ツールごとに状態・一言・使い方・確かめ方・止まっている理由。`server.py` が毎回読んで `window.LIFEOS`、オンラインは DB の `lifeos`。
- `desk/sync.py merge` に Mac → オンラインの一方向を追加（`remote.json` に `lifeos` があるときだけ。`HOURLY_SYNC.md` も更新）。
- `lifeos/lib/`：パス、Desk タスク、追記ログ、Markdown、ツールの読み込み。
- `lifeos/bot/`：`handler.py` の自動読み込み、振り分け、`cli.py`、long polling の `run.py`、差し替え可能な音声。

## 決めたこと
- `LIFEOS_DATA` を別の場所に向けると sandbox：タスクもその中に閉じ、Desk サーバーも Telegram も呼ばない。テストが本物を触らないため。
- `/resume` は標準とぶつかるので、15番のコマンドは `/tsuzuki`。
- `tasks.update` は Desk が持たない項目を拒否する。`taskRecord` が次の編集で消すため（優先度などは `note` へ）。
- `chat_id` が未設定のボットは、チャット ID を返すだけで何も処理しない。トークンが漏れても他人に使わせないため。
- `index.html` の表示の分岐は既存の長い行を変えず、`render()` の手前の1行で早く返す。別ブランチの未コミットの変更と衝突させないため。
- 朝のブリーフィングには各ツールの `brief.py` から混ぜる。01番のファイルを共有しないため。

## 中断した場所
- `lifeos/base` にコミット済み。main へのマージ・push・オンライン公開はしていない。

## 次の一手
- 統括がツールごとの作業を始める。共有ファイルの変更依頼は各ツールの `INTEGRATION.md` に集まる。

## 覚えておくこと
- サンドボックスの Bash ではローカルのポートを開けない。Desk の API テストはその場合スキップされる。
- `desk/index.html` には `viewport` の指定がない（Artifact 側で付く）。localhost で 375px を見ると 980px 幅になるので、確認は viewport を足した写しで行う。
