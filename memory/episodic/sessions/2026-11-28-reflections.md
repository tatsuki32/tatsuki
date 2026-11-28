# 2026-11-28 遅れの振り返り（Reflections）

## ひとこと
- いま：反省文の画面ができた
- 次：本人が1件書いて公開
- 待ち：なし

## やったこと
- `/desk-feature` の流れ。調べた方法（SRE の事後検証、5 Whys とその批判、自分を許す研究、If-then、計画の誤り、KPT）を要件定義に出典つきで入れ、承認をもらった。
- `desk/reflect.py`：遅れ・遅れて完了・期日の後ろずらし・返信の遅れ・Promise の習慣と目標・お金の上限と赤字を10分ごとに見つける。Claude（Sonnet 5.5）が話した言語で書き上げる。公開は押したときだけ。
- `server.py`：コレクション `reflections`、期日を後ろへ動かすとタスクに `dueMoves` を残す（`note_due_move`）。
- Rhizome Box：`PUT /_/api/desk/reflection`（秘密 `REFLECT_KEY`）。被リンクと改名の検索を LIKE から `instr` に（D1 が長い題の LIKE を断って 500 になっていた）。
- 試験：`desk/tests/test_reflect.py`（11件）、`rhizomebox` の `npm test`（39件）。試験サーバーで Home のカード → カード8枚 → 書き上げ → 公開 → 未ログインで読める → Unpublish → Try のタスクまで確認。375px で横スクロールなし。

## 決めたこと
- 印（Promise）のない習慣は何も出さない（習慣で責めない決まりを守るため）。
- Home のカードは件数と上の3件。全部は `#reflect`。
- 署名の既定は Tatsuki、言語は話した言語のまま（本人に未確認、設定で変えられる）。

## 残り
- Worker の deploy と `bash desk/reflect_setup.sh` は済んだ（同じ日、本人の OK のあと）。
- 本物の遅れたタスク1件で本人が書いて公開する。

## 追加（同じ日、本人の希望）
- Home のいちばん上に出す、Desk を開いたときのポップアップで最初の問いを聞く（× で閉じる）、反省文ごとに Tasks に「Write a reflection: …」のタスク（書き終えると Done）。
- カードの画面を作り直した（幅720px、事実のタイル、問いを見出しに、下に Back／Next、⌘Enter で次）。3枚目に Claude の見立て（仮説2〜3つ、根拠と確かめる問い、This fits／Not this、本人の意見）。材料は件の履歴と、Mac だけに置く `desk/data/reflect/context.md`。
- Speak が Desk の窓で動かなかった（WKWebView に音声認識がない）。`desk/voice.py`：押すと TatsukiMeetWatch がマイクを録り、Stop で Mac の whisper-cli が文字にする。本物のマイクで録音、`say` の音声で文字になることを確認（約5秒）。
- 公開ボタンが何もしなかった：Desk の窓では confirm() が出ず false になる。公開は「もう一度押す」、Not a miss は画面内の入力欄にした。サーバーは Mac で直接コミットしたため再起動されず古いままだったので kickstart した（`memory/semantic/patterns/desk-app-window.md`）。1件目は公開済み。
