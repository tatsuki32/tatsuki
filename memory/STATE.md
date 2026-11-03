# 今の状態（STATE）

最終更新：2026-11-03（ブランチ `claude/vigilant-babbage-033h8w`）

## 進行中の作業
- 作りたい機能リスト（全11件）を順番に作っている。Desk のタスク「作りたい機能①〜⑪」で進み具合を見る。
  - ① CLAUDE.md＋memory/（外付けメモリ）… 実装済み・マージ待ち
  - 次は ② /morning-kickoff → ③ /resume → ④ RSD対策テンプレ …
- ブランチ `claude/vigilant-babbage-033h8w` が main に未マージ（URL変更ボタン、Scrapbox風リンク、memory/）。Mac に届くのはマージ後。

## 決定済みの事項
- 機能追加は `/desk-feature` の流れ（質問 → 要件定義 → 承認 → 実装）。質問はすべて日本語で聞く。
- Claude Code 用の機能（コマンド・ルール・フック）はこのリポジトリに置く（`.claude/`、`memory/`）。
- Desk のリンクの色は Scrapbox 式：青＝つながっている（中身のあるノートがある or 2か所以上で使用）、赤＝孤立。外部リンクは金色。
- URL が変わるツールは `tools` に `urlEditable:true`。✏️ はオンライン版だけ。

## よくあるハマりどころ
- このリポジトリは公開。個人データ・ツールのURL・健康のことは書かない（`memory/README.md`）。
- Artifact の publish は先に `action: read`。読んだだけでは「見た」扱いにならず1回拒否されることがある → 差分が自分の変更だけなら同じ内容で再送すると通る。
- クラウドからは trycloudflare などの外部 URL に届かない（プロキシで 403）。
