#!/bin/sh
# 外付けメモリの書き忘れ防止（Claude Code の SessionStart / Stop フック）。
# SessionStart：開始時の HEAD を覚える。
# Stop：このセッションでファイルを変えたのに memory/episodic/sessions/ が変わっていなければ、1回だけ記録を促す。
# 標準のシェルと git だけで動く（Mac / クラウド共通）。
input=$(cat)
sid=$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
[ -n "$sid" ] || exit 0
dir="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$dir" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
state="${TMPDIR:-/tmp}/claude-memory-hook"
mkdir -p "$state" 2>/dev/null || exit 0
start="$state/$sid.start"; done="$state/$sid.reminded"

if [ "$1" = "start" ]; then
  [ -f "$start" ] || git rev-parse HEAD > "$start" 2>/dev/null
  exit 0
fi

# Stop
printf '%s' "$input" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0
[ -f "$done" ] && exit 0
[ -f "$start" ] || exit 0
base=$(cat "$start")
changed=$( { git diff --name-only "$base" 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } | sort -u)
[ -n "$changed" ] || exit 0
printf '%s\n' "$changed" | grep -q '^memory/episodic/sessions/' && exit 0
printf '%s\n' "$changed" | grep -qv '^memory/' || exit 0
touch "$done"
cat <<'JSON'
{"decision":"block","reason":"このセッションでファイルを変更しましたが、memory/episodic/sessions/ に記録がありません。memory/README.md の雛形でセッション記録（やったこと・決めたこと・中断した場所・次の一手）を書き、必要なら memory/STATE.md も更新してから終えてください。公開リポジトリなので個人データ・URL・健康のことは書かないこと。"}
JSON
exit 0
