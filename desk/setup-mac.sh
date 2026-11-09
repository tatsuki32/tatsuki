#!/bin/bash
# Tatsuki's Desk — one-time Mac setup. Run from anywhere:
#   bash desk/setup-mac.sh
# Installs two background jobs for this user (no sudo):
#   com.tatsuki.desk.server  keeps http://localhost:4747 running (starts at login)
#   com.tatsuki.desk.pull    desk/pull.sh every 5 minutes: git pull main, and restart the server when server.py changed
# Run it again any time; it replaces the old jobs. Remove with: bash desk/setup-mac.sh --uninstall
set -euo pipefail

DESK="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DESK/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs"
PORT="${DESK_PORT:-4747}"
BRANCH="${DESK_BRANCH:-main}"
PY="$(command -v python3 || true)"
GIT="$(command -v git || true)"
UID_="$(id -u)"

unload() { launchctl bootout "gui/$UID_/$1" 2>/dev/null || true; rm -f "$AGENTS/$1.plist"; }

if [[ "${1:-}" == "--uninstall" ]]; then
  unload com.tatsuki.desk.server; unload com.tatsuki.desk.pull
  rm -f "$HOME/Desktop/Tatsuki's Desk.html"
  echo "Desk の常駐ジョブを削除しました。$DESK/data のデータはそのまま残っています。"; exit 0
fi

[[ -n "$PY" ]] || { echo "python3 が見つかりません。ターミナルで xcode-select --install を実行してから、もう一度どうぞ。"; exit 1; }
[[ -n "$GIT" ]] || { echo "git が見つかりません。xcode-select --install を実行してください。"; exit 1; }
mkdir -p "$AGENTS" "$LOGS" "$DESK/data"

# macOS blocks background jobs from reading Documents / Desktop / Downloads / iCloud Drive
case "$REPO/" in
  "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*|"$HOME/Library/Mobile Documents/"*)
    echo "このフォルダ（$REPO）は macOS の保護フォルダの中なので、自動起動したサーバーが読めません。"
    echo "ホーム直下などに移してから、もう一度実行してください。例："
    echo "  mv \"$REPO\" ~/TatsukiDesk && bash ~/TatsukiDesk/desk/setup-mac.sh"
    exit 1;;
esac

# stop our own jobs first, then make sure nothing else holds the port (the old Desk server)
unload com.tatsuki.desk.server; unload com.tatsuki.desk.pull
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ポート $PORT を別のプログラムが使っています（古い Desk サーバーの可能性）："
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
  echo "それを止めてから（古いログイン項目・launchd・ターミナルで動いている server を終了）、もう一度実行してください。"
  exit 1
fi

# bring over notes, due dates and tasks from the old Mac desk once, if they exist and look right
OLD="$HOME/Documents/ClaudeTools/data"
for c in memos dues tasks; do
  if [[ -f "$OLD/$c.json" && ! -f "$DESK/data/$c.json" ]]; then
    if "$PY" -c "import json,sys; v=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(v,list) and all(isinstance(x,dict) and x.get('id') for x in v) else 1)" "$OLD/$c.json"; then
      cp "$OLD/$c.json" "$DESK/data/$c.json"; echo "旧版の $c.json を取り込みました"
    else
      echo "旧版の $c.json は形式が違うので取り込みませんでした（毎時の同期でオンライン版から入ります）"
    fi
  fi
done

cat > "$AGENTS/com.tatsuki.desk.server.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tatsuki.desk.server</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DESK/server.py</string></array>
  <key>EnvironmentVariables</key><dict><key>DESK_PORT</key><string>$PORT</string></dict>
  <key>WorkingDirectory</key><string>$DESK</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOGS/tatsuki-desk.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tatsuki-desk.log</string>
</dict></plist>
PLIST

cat > "$AGENTS/com.tatsuki.desk.pull.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tatsuki.desk.pull</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DESK/pull.sh</string></array>
  <key>EnvironmentVariables</key><dict><key>DESK_BRANCH</key><string>$BRANCH</string><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$LOGS/tatsuki-desk-pull.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tatsuki-desk-pull.log</string>
</dict></plist>
PLIST

launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.server.plist"
launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.pull.plist"
# 漏洩防止のコミット前フック（.githooks/pre-commit）を有効にする
git -C "$REPO" config core.hooksPath .githooks

for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1 && break; sleep 0.5
done
# double-click launcher on the Desktop (opens the Mac desk, or offers the online one if the server is down)
LAUNCHER="$HOME/Desktop/Tatsuki's Desk.html"
sed "s/localhost:4747/localhost:$PORT/g" "$DESK/launcher.html" > "$LAUNCHER" && echo "デスクトップに「Tatsuki's Desk.html」を置きました（ダブルクリックで開けます）"

if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
  echo "✅ Desk が起動しました: http://localhost:$PORT"
  open "http://localhost:$PORT" 2>/dev/null || true
else
  echo "サーバーが起動しませんでした。ログ: $LOGS/tatsuki-desk.log"; exit 1
fi
[[ "$("$GIT" -C "$REPO" rev-parse --abbrev-ref HEAD)" == "$BRANCH" ]] || \
  echo "⚠️ このフォルダは $BRANCH ブランチではないので、自動 git pull は止まっています（git checkout $BRANCH で再開）。"
