# 10 決断ゼロ起動

## 目的

作業を始めるときに、自分で選ぶものをなくします。
セッションを始めると、ルールと今日のブリーフィングが読まれ、最初の1件だけが出ます。
長くさわっていないタスクは自動で「延期」に移し、目の前の候補を減らします。

## 動き

| いつ | 何をする |
| --- | --- |
| セッション開始（新規と `/clear`） | CLAUDE.md と `briefs/今日.md` を読み、最初の1件を出す |
| その日の最初のセッション | 14日以上さわっていないタスクを延期に移す |
| 前回から7日以上たったセッション | 延期中のタスクをまとめて出す |

- 最初の1件は `desk/kickoff.py` の候補から、`/morning-kickoff` と同じ順番で選びます。今日が締切、前回の「次の一手」、進行中、残りの順です。
- 延期は Desk の `archived` を使います。Desk のアーカイブ棚に出ます。消しません。
- 「さわった」は、Desk やツールで編集された時刻（`updatedAt`）です。開いて見ただけでは数えません。

### 延期しないもの

- 締切がこれから来るタスクと、締切を過ぎて14日未満のタスク。隠すと締切を逃すからです。
- メール・カレンダー・Google Tasks など、外から来たタスク。本当の締切は外にあります。
- 時刻の記録がないタスク。

## 使い方

ふだんは何もしません。フックが動きます。手で動かすときは次のとおりです。

```sh
python3 lifeos/10-autostart/autostart.py first         # 最初の1件を見る（何も変えない）
python3 lifeos/10-autostart/autostart.py defer --dry   # 延期になるものを見る（何も変えない）
python3 lifeos/10-autostart/autostart.py defer         # 今すぐ延期する
python3 lifeos/10-autostart/autostart.py review        # 延期中のものを見る
python3 lifeos/10-autostart/autostart.py restore <ID>  # 1件戻す（--all で全部）
```

戻すと元の状態に返り、そこからまた14日数えます。Desk で状態を変えても同じです。

日数を変えるときは `desk/data/lifeos/tools/10-autostart/config.json` に `{"days": 21}` と書きます。

## 止め方

```sh
python3 lifeos/10-autostart/autostart.py off         # 全部止める（on で再開）
python3 lifeos/10-autostart/autostart.py defer-off   # 延期だけ止める（defer-on で再開）
```

`tool.json` の `status` を `stopped` にしても止まります。完全に外すなら `.claude/settings.json` のフックを消します。
止めても、延期済みのタスクはアーカイブに残ります。戻すなら先に `restore --all` です。

## 置き場

設定と状態は `desk/data/lifeos/tools/10-autostart/` の `config.json` と `state.json` です。git には入りません。

## フック

`.claude/settings.json` の SessionStart に入っています。動く範囲と注意は `INTEGRATION.md` にあります。
