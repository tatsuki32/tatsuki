#!/bin/bash
# Tatsuki's Desk — one-time Mac setup. Run from anywhere:
#   bash desk/setup-mac.sh
# Installs the app and four background jobs for this user (no sudo):
#   Tatsuki's Desk.app       the window you open: ~/Applications, with an alias on the Desktop
#   com.tatsuki.desk.server  keeps http://localhost:4747 running (starts at login)
#   com.tatsuki.desk.pull    desk/pull.sh every 5 minutes: git pull main, and restart the server when server.py changed
#   com.tatsuki.desk.backup  every 15 minutes, desk/data goes to its private repository (backup_data.py)
#   com.tatsuki.desk.wise    every morning and at login, Wise activity comes to desk/data/wise (wise_sync.py, read only)
#   com.tatsuki.desk.screentime  every hour, Screen Time totals come to desk/data/screentime (screentime_sync.py, read only)
#   com.tatsuki.desk.telegram    the Telegram bot: photos and messages reach this Mac (lifeos/bot/run.py). Without a token it waits and tries again
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
  unload com.tatsuki.desk.server; unload com.tatsuki.desk.pull; unload com.tatsuki.desk.backup; unload com.tatsuki.desk.wise; unload com.tatsuki.desk.screentime; unload com.tatsuki.desk.tabwatch; unload com.tatsuki.desk.telegram
  rm -f "$HOME/Desktop/Tatsuki's Desk.html" "$HOME/Desktop/Tatsuki's Desk.app"
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
unload com.tatsuki.desk.server; unload com.tatsuki.desk.pull; unload com.tatsuki.desk.backup; unload com.tatsuki.desk.wise; unload com.tatsuki.desk.screentime; unload com.tatsuki.desk.tabwatch; unload com.tatsuki.desk.telegram
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ポート $PORT を別のプログラムが使っています（古い Desk サーバーの可能性）："
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
  echo "それを止めてから（古いログイン項目・launchd・ターミナルで動いている server を終了）、もう一度実行してください。"
  exit 1
fi

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

cat > "$AGENTS/com.tatsuki.desk.backup.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tatsuki.desk.backup</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DESK/backup_data.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>900</integer>
  <key>StandardOutPath</key><string>$LOGS/tatsuki-desk-backup.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tatsuki-desk-backup.log</string>
</dict></plist>
PLIST

cat > "$AGENTS/com.tatsuki.desk.wise.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tatsuki.desk.wise</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DESK/wise_sync.py</string></array>
  <key>WorkingDirectory</key><string>$DESK</string>
  <key>RunAtLoad</key><true/>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$LOGS/tatsuki-desk-wise.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tatsuki-desk-wise.log</string>
</dict></plist>
PLIST

[[ -x "$HOME/Applications/TatsukiScreenTime" ]] || { mkdir -p "$HOME/Applications"; swiftc -O "$DESK/app/screentime-helper.swift" -o "$HOME/Applications/TatsukiScreenTime"; }
cat > "$AGENTS/com.tatsuki.desk.screentime.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tatsuki.desk.screentime</string>
  <key>ProgramArguments</key><array><string>$HOME/Applications/TatsukiScreenTime</string></array>
  <key>WorkingDirectory</key><string>$DESK</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>$LOGS/tatsuki-desk-screentime.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tatsuki-desk-screentime.log</string>
</dict></plist>
PLIST

[[ -x "$HOME/Applications/TatsukiTabWatch" ]] || swiftc -O "$DESK/app/tabwatch.swift" -o "$HOME/Applications/TatsukiTabWatch" -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$DESK/app/tabwatch-info.plist" && codesign -f -s - -i com.tatsuki.desk.tabwatch "$HOME/Applications/TatsukiTabWatch"
cat > "$AGENTS/com.tatsuki.desk.tabwatch.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tatsuki.desk.tabwatch</string>
  <key>ProgramArguments</key><array><string>$HOME/Applications/TatsukiTabWatch</string></array>
  <key>StartInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$LOGS/tatsuki-desk-tabwatch.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tatsuki-desk-tabwatch.log</string>
</dict></plist>
PLIST

launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.tabwatch.plist"
launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.screentime.plist"
cat > "$AGENTS/com.tatsuki.desk.telegram.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tatsuki.desk.telegram</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$REPO/lifeos/bot/run.py</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$LOGS/tatsuki-desk-telegram.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tatsuki-desk-telegram.log</string>
</dict></plist>
PLIST
launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.telegram.plist"
launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.server.plist"
launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.backup.plist"
launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.pull.plist"
launchctl bootstrap "gui/$UID_" "$AGENTS/com.tatsuki.desk.wise.plist"
# 漏洩防止のコミット前フック（.githooks/pre-commit）を有効にする
git -C "$REPO" config core.hooksPath .githooks

for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1 && break; sleep 0.5
done
# the window you open. If it cannot be built the Desk still works in a browser
rm -f "$HOME/Desktop/Tatsuki's Desk.html"
if APP="$(bash "$DESK/app/build.sh" "$HOME/Applications")"; then
  ln -sfn "$APP" "$HOME/Desktop/Tatsuki's Desk.app"
  echo "デスクトップに「Tatsuki's Desk」を置きました。Dock に入れるときは、そこへドラッグしてください。"
else
  echo "アプリを作れませんでした。ブラウザで http://localhost:$PORT を開くと使えます。"
fi

if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
  echo "✅ Desk が起動しました: http://localhost:$PORT"
  [[ -n "${APP:-}" ]] && open "$APP" 2>/dev/null || true
else
  echo "サーバーが起動しませんでした。ログ: $LOGS/tatsuki-desk.log"; exit 1
fi
[[ "$("$GIT" -C "$REPO" rev-parse --abbrev-ref HEAD)" == "$BRANCH" ]] || \
  echo "⚠️ このフォルダは $BRANCH ブランチではないので、自動 git pull は止まっています（git checkout $BRANCH で再開）。"
