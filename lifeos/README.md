# Life OS 各ツール共通の約束

18個の小さなツールを `lifeos/NN-slug/` に1個ずつ置きます。ツールの担当はこのファイルだけ読めば作業できます。
各ツールの仕様は自分の `tool.json` と、統括から渡された仕様書の該当部分です。

## 編集してよい場所（これだけ）

1. 自分の `lifeos/NN-slug/`
2. 自分用の `.claude/commands/<名前>.md`（名前は自分の `tool.json` の `commands` にあるものだけ）
3. `lifeos/tests/test_NN_*.py`（NN は自分の番号）
4. `memory/episodic/sessions/` の新規ファイル（`YYYY-MM-DD-lifeos-NN-slug.md`）

`desk/`、`lifeos/lib/`、`lifeos/bot/`、`CLAUDE.md`、`memory/STATE.md`、`.claude/settings.json`、ほかのツールのフォルダは触りません。
同時に最大10個の作業が走るので、1つでも触ると衝突します。

### 共有ファイルを変えたいとき

自分のフォルダの `INTEGRATION.md` に、どのファイルをどう変えるかをそのまま適用できる形で書きます。
統括がマージのあとで入れます。フック（10番・15番）、定期実行、Desk の画面やサーバーの変更はこの形で頼みます。
ライブラリに足りない機能があれば、自分のフォルダで済ませて `INTEGRATION.md` に一言書きます。

## 作業の進め方

1. `tool.json` の `status` を `building` にする。
2. 作る。各ツールは単体で動くようにする。
3. `lifeos/tests/test_NN_*.py` を書いて通す（下の「テスト」）。
4. `tool.json` を実物に合わせて直す。本人が確かめられる手順を `howToCheck` に書く。
5. 動いたら `status` を `working` にする。外の都合で進めないなら `blocked` にして、`blockers` に書く。推測で埋めない。
6. セッション記録を書き、自分のブランチにコミットする。マージ・push・オンライン公開はしない。

コミットの1行目は日本語で、何をしたか分かる60字まで。`--no-verify` は使いません。フックが止めたら中身を直します。

## tool.json

| 項目 | 中身 |
| --- | --- |
| `no` | 番号の文字列（`"03"`） |
| `name` / `oneline` | 名前 / 一言 |
| `status` | `planned` 予定 / `building` 作っている / `working` 動いている / `blocked` 止まっている / `stopped` 止めた |
| `usage` | 使い方の配列 |
| `howToCheck` | 本人の確認手順の配列。専門用語を使わず、1行に1つの動作 |
| `blockers` | `{what, why, needs}` の配列。何が・なぜ・本人に何をしてほしいか |
| `commands` | 自分のスラッシュコマンド |
| `updated` | 最終更新日 `YYYY-MM-DD` |

Desk の「Life OS」画面はこのファイルをそのまま出します。`status` が `stopped` のツールはボットに読み込まれません。
画面に出る文なので、日本語で短く書きます。実名・会社名・案件名は書きません。

## データの置き場

個人データは git に入れず、`LIFEOS_DATA`（既定 `~/TatsukiDesk/desk/data/lifeos`）に置きます。worktree から動かしても本体のデータを指します。

| 場所 | 中身 | 書く担当 |
| --- | --- | --- |
| `inbox/` | 届いた未整理メモ | 04 |
| `notes/` | 整理済みメモ | 04・11 |
| `life/` | 分野別ファイル | 14 |
| `briefs/YYYY-MM-DD.md` | 朝のブリーフィングとまとめ | 01・11 |
| `logs/<名前>.jsonl` | 追記ログ（`habits`・`expenses`・`tidy`・`work`・`wins` など） | そのログの担当 |
| `tools/NN-slug/` | そのツールだけの設定と状態 | 各担当 |
| `secrets/` | トークン類 | 本人 |

```python
from lifeos.lib import logs, md, paths

logs.append("habits", {"name": "読書", "minutes": 30})      # logs/habits.jsonl に1行足す
logs.read("habits", limit=20)                               # 新しい20件
md.save(f"briefs/{paths.today()}.md", "# 今日の3つ\n...")    # 上書き保存
md.append("logs/wins.md", "- できたこと")                    # 追記
md.read("life/health.md")                                   # 本文。なければ None
paths.data_path("tools", "08-tidy", "places.json")          # それ以外のファイルはこのパスで
```

時刻はホーチミン時間です（`paths.now()`・`paths.today()`）。データ置き場の外には書けません。

## タスク

タスクの置き場は Desk の `tasks` だけです。`tasks/MASTER_PLAN.md` は作りません。
サーバーが動いていれば API 経由、止まっていれば `tasks.json` に直接書きます。`updatedAt` が付くので毎時同期に乗ります。

```python
from lifeos.lib import tasks

t = tasks.add("資料を送る", due=None, note="優先度:高 案件:例の案件", source="lifeos-03")
tasks.open_tasks()                   # notyet と doing
tasks.get(t["id"])
tasks.update(t["id"], status="doing")
tasks.done(t["id"])
tasks.delete(t["id"])                # deleted:true にするだけ
```

- `status` は `notyet` / `doing` / `done` / `archived` / `dropped`。
- `due` はミリ秒の時刻か `None`。
- 項目は Desk と同じものだけです。Desk で編集すると、ほかの項目は消えます。優先度や案件名は `note` に書きます。

## ボット（Telegram）

`lifeos/NN-slug/handler.py` を置くと自動で読み込まれます。ボット本体は触りません。

```python
PRIORITY = 10  # 省略すると 50。小さいほど先

def match(text):
    return text.lower().startswith("task:")

def handle(text, ctx):
    t = ctx.tasks.add(text.split(":", 1)[1], source="lifeos-03")
    return f"追加しました：{t['title']}"   # 返事。None なら返事なし
```

`ctx` には `source`（`telegram` か `cli`）、`chat_id`、`is_voice`、`send(文字)`、`tasks`、`logs`、`md`、`paths` があります。
`match` は速く、副作用なしで書きます。音声は文字にしてから同じ `match` と `handle` に渡します。
`match` が受け取るのは文字だけです。音声かどうかは `handle` の `ctx.is_voice` で分かります。

最初に `match` が真を返したツールが受けます。同じ順位なら番号順です。

| PRIORITY | ツール | 受けるもの |
| --- | --- | --- |
| 10 | 03・08 | はっきりした合図（`task:`、`片付け`、`済`） |
| 20 | 14 | 問いかけへの答え（問いの直後だけ） |
| 30 | 07 | 習慣の記録 |
| 40 | 12 | 支出・締切 |
| 60 | 17 | ブレインダンプの合図（先頭の言葉で見分ける） |
| 90 | 04 | 残り全部 |

どのツールも受けなければ `logs/unrouted.jsonl` に残ります。ツールが失敗しても文面はそこに残り、ほかのツールは動き続けます。

```sh
python3 lifeos/bot/cli.py "task: 資料を送る"          # トークンなしで試す
python3 lifeos/bot/cli.py --which "task: 資料を送る"  # どのツールが受けるかだけ見る
python3 lifeos/bot/cli.py --list                     # 振り分け順と、読み込めないツール
python3 lifeos/bot/run.py                            # 本番（トークンがなければ案内を出して終わる）
```

こちらから送るときは次のようにします。トークンがない間は `logs/outbox.jsonl` に残ります。

```python
from lifeos.bot.telegram import notify
notify("今週の習慣です")
```

音声は `lifeos/bot/voice.py` の関数を差し替えて接続します。未接続なら「文字起こし未接続」と返します。

トークンは環境変数か `secrets/telegram.json` だけに置きます。リポジトリにもログにも書きません。

## 朝のブリーフィングに混ぜる

01番を触らずに混ぜられます。`lifeos/NN-slug/brief.py` に次を置きます。01番は `tools.brief_parts()` で集めます。

```python
def lines(day):
    return ["返信待ち：2件"]   # 短く。何もなければ空の配列
```

## ほかのファイルを読み込む

フォルダ名に数字とハイフンがあるので `import` は使えません。

```python
from lifeos.lib.tools import load_tool
core = load_tool("04-voice", "core")   # lifeos/04-voice/core.py
```

コマンドから動かす Python は、先頭で `sys.path` にリポジトリのルートを足します（`lifeos/bot/cli.py` の先頭と同じ）。
実行環境は Python 3.9 の標準ライブラリだけです。この Mac に node はありません。

## テスト

```sh
python3 -m unittest discover -s lifeos/tests -t .
python3 -m unittest lifeos.tests.test_03_task     # 自分のだけ
```

`lifeos/tests/sandbox.py` の `SandboxCase` を継承します。一時フォルダを `LIFEOS_DATA` にするので、タスクも `tasks.json` もその中に閉じ、Desk サーバーにも Telegram にもつながりません。

```python
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

class TestTask(SandboxCase):
    def test_add(self):
        h = load_tool("03-task", "handler")
        self.assertTrue(h.match("task: 試し"))
```

本物のデータでテストしません。手で試すときも `LIFEOS_DATA="$TMPDIR/try"` を付けます。
本体の作業ツリーとポート 4747 のサーバーには触りません。

## 外から来るものを扱うとき

- メール本文・予定・メモの中身は `LIFEOS_DATA` か Desk の DB にだけ置きます。リポジトリ、`memory/`、`tool.json` には書きません。
- 送信・お金・削除など戻せない操作は、必ず本人の承認を挟みます。下書きまでで止めます。
- 外部サービスの認証がなくて進めない部分は、作ったところまでコミットして `blocked` にします。
- 習慣と片付けでは、責める言葉・赤・リセットを出しません。「今やること」は常に3件までです。

## コマンドの持ち主

`.claude/commands/` は名前ごとに持ち主を決めています。`tool.json` の `commands` が正本です。

`/morning` 01 · `/task` `/next` `/done` `/save` 03 · `/lines` 05 · `/kickoff` 06 · `/newtool` 09 · `/tsuzuki` `/split` 15 · `/where` `/lowenergy` 16 · `/subs` 18

`/resume` は Claude Code の標準とぶつかるので、15番の `/tsuzuki` に置き換えました。既存の `/begin` `/knowledge` `/learn` `/morning-kickoff` `/recap` `/rsd` `/sync` は使いません。

## 終わる前に

- [ ] テストが全部通る（`python3 -m unittest discover -s lifeos/tests -t .`）
- [ ] 編集した場所が「編集してよい場所」の中だけ（`git status` で確かめる）
- [ ] `tool.json` の `status`・`usage`・`howToCheck`・`blockers`・`updated` が実物と合っている
- [ ] 実名・会社名・案件名・キー・個人データをコミットしていない
- [ ] セッション記録を書いた
