# Desk をオンライン版に公開する手順と注意点

1. `desk/index.html` を編集 → Playwright でモックDB（`window.claude.use("db")` を差し替え）を使って 375px と 1200px で確認。
   - モックは `collection(name).onSnapshot / doc(id).update|set|get / orderBy / limit / add` があれば動く。
2. コミット → `git push -u origin <ブランチ>`。
3. Artifact `action: read`（URL は CLAUDE.md）で最新版を読む。claude.ai 側の変更がないか、保存ファイルと `git show HEAD~1:desk/index.html` を比べる。
4. Artifact publish（`file_path: desk/index.html` と `url`）。`capabilities` は渡さない。
   - 「live version を見ていない」で拒否されたら、差分が自分の変更だけか確かめて同じ内容で再送する。
5. DB の行（`tools`、`tasks` など）は ArtifactData で直接書く。既存の行の更新には `if_version` が必要。

## Artifact で使えないもの
- `prompt()` / `confirm()` / `alert()` は何も出ずに終わる。入力は `#modal` のダイアログ（`askText()`）、確認は2回押しにする。
