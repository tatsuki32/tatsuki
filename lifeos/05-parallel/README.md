# 05 並列ライン

複数の Claude Code セッションを同時に回すための道具です。1作業を1ブランチ・1作業フォルダ（git worktree）に分け、1画面で一覧します。

長時間の稼働とやりかけの増えすぎを防ぐため、安全装置を3つ付けています。

- 作りかけは最大3つまで。止めているラインも数えます。
- 始めるときに終了時刻を決めます。過ぎると止めるよう通知します。
- 稼働時間を記録し、週ごとに見せます。

## 使い方

Claude Code で `/lines` と打つと、一覧が出ます。直接動かすときは `python3 lifeos/05-parallel/lines.py` です。

| やりたいこと | 打つもの |
| --- | --- |
| 一覧を見る | `/lines` |
| 始める | `/lines start 名前 --until 18:30 --what "何をするか"` |
| 進み具合を書く | `/lines note 名前 "テストが通った"` |
| 止める | `/lines pause 名前 "理由"` |
| 再開する | `/lines resume 名前 --until 20:00` |
| 終了時刻を延ばす | `/lines extend 名前 30m` |
| 終える | `/lines end 名前` |
| 週の稼働時間を見る | `/lines week` |
| すべての worktree を見る | `/lines all` |
| 終えたラインの作業フォルダを消す | `/lines remove 名前`（内容を確かめてから `--yes`） |

- `start` は `lines/名前` のブランチと `.worktrees/名前` の作業フォルダを作ります。`--branch` で既存のブランチも使えます。
- 終了時刻は `18:30` か `90m` で書きます。12時間より先は受け付けません。
- 終了時刻は `end` のときに `--at 18:30` で直せます。止め忘れた分を稼働時間から除けます。
- 上限は `lines.py limit 4` で変えられます。5までです。

## 通知の流れ

終了時刻を過ぎると、Telegram と Mac の通知に送ります。30分おきに計3回までで、そのあとは一覧にだけ出します。

一覧の知らせは毎回状態から作るので、Telegram がなくても出ます。Telegram が未接続のときの文面は `logs/outbox.jsonl` に残ります。

見張りは `start` のときに裏で自動で動き、動いているラインがなくなると終わります。Mac を再起動すると止まりますが、次に `/lines` を打つと戻ります。

## できないこと

- Claude Code を自動で止めることはしません。通知までです。
- `start` せずに始めたセッションは上限に数えません。一覧の下に本数だけ出します。
- 「どこまで」は `note` の内容か最新のコミットです。セッションの中を読んではいません。
- 対象は1つのリポジトリです。別のリポジトリは `LIFEOS_LINES_REPO` で切り替えます。

## データ

すべて `LIFEOS_DATA` の中にあり、git には入りません。

- `tools/05-parallel/lines.json`：ラインの状態
- `tools/05-parallel/config.json`：上限
- `logs/work.jsonl`：稼働の記録
- `logs/lines.jsonl`：始めた・延ばした・通知したの記録

稼働時間は、ラインが1つでも動いていた時間です。重なりは1回だけ数え、止めている時間は数えません。

## 止め方

1. 開いているラインをすべて `/lines end 名前` で終えます。見張りはそこで終わります。
2. ずっと使わないなら `tool.json` の `status` を `stopped` にします。

これで裏で動くものはなくなります。作業フォルダとブランチは残ります。

## 試すとき

```sh
LIFEOS_DATA="$TMPDIR/try" LIFEOS_LINES_REPO=/試し用のリポジトリ python3 lifeos/05-parallel/lines.py
```

`LIFEOS_DATA` を変えると Telegram も Mac の通知も見張りも動きません。見張りも試すなら `LIFEOS_LINES_WATCHER=1` を足します。

Desk の画面への表示などの共有ファイルの変更は `INTEGRATION.md` にあります。
