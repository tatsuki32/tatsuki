#!/bin/bash
# Tatsuki's Desk — Wi-Fi Login: the Desktop icon and the watcher. Run from anywhere:
#   bash desk/wifilogin-setup.sh
# Installs two things for this user (no sudo, no system setting is changed):
#   Wi-Fi Login.app             ~/Applications, with an alias on the Desktop. Double-click: finds the Wi-Fi's sign-in page
#                               and opens it in Safari (desk/wifi_login.py app). Works with the Desk server stopped.
#   com.tatsuki.desk.wifilogin  when the network changes, desk/wifi_login.py auto looks once and opens Safari only if
#                               a sign-in is needed. Nothing runs in between.
# Run it again any time; it replaces both. Remove with: bash desk/wifilogin-setup.sh --uninstall
set -euo pipefail

DESK="$(cd "$(dirname "$0")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs"
LABEL="com.tatsuki.desk.wifilogin"
APP="$HOME/Applications/Wi-Fi Login.app"
ALIAS="$HOME/Desktop/Wi-Fi Login.app"
UID_="$(id -u)"

launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$AGENTS/$LABEL.plist" "$ALIAS"; rm -rf "$APP"
  echo "Wi-Fi Login を削除しました。"; exit 0
fi
mkdir -p "$AGENTS" "$LOGS" "$HOME/Applications"

# the icon you double-click: an app whose whole program is one line
WORK="$(mktemp -d "${TMPDIR:-/tmp}/wifilogin.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/App.app/Contents/MacOS" "$WORK/App.app/Contents/Resources"
cat > "$WORK/App.app/Contents/MacOS/WiFiLogin" <<SH
#!/bin/sh
exec /usr/bin/python3 "$DESK/wifi_login.py" app
SH
chmod +x "$WORK/App.app/Contents/MacOS/WiFiLogin"
cat > "$WORK/App.app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Wi-Fi Login</string>
  <key>CFBundleDisplayName</key><string>Wi-Fi Login</string>
  <key>CFBundleIdentifier</key><string>com.tatsuki.desk.wifilogin.app</string>
  <key>CFBundleExecutable</key><string>WiFiLogin</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
# the icon is a nicety: the app works without it
if swift "$DESK/app/wifilogin-icon.swift" "$WORK/icon.png" 2>/dev/null; then
  SET="$WORK/AppIcon.iconset"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$WORK/icon.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z "$((s*2))" "$((s*2))" "$WORK/icon.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$WORK/App.app/Contents/Resources/AppIcon.icns" || true
fi
codesign --force --sign - "$WORK/App.app" 2>/dev/null || true
rm -rf "$APP"
mv "$WORK/App.app" "$APP"
ln -sfn "$APP" "$ALIAS"

# the watcher: macOS rewrites resolv.conf when it joins or leaves a network. Only that file is watched:
# /Library/Preferences/SystemConfiguration changes every time networksetup runs, so watching it made the job start itself over and over
cat > "$AGENTS/$LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>$DESK/wifi_login.py</string><string>auto</string></array>
  <key>WatchPaths</key><array><string>/private/var/run/resolv.conf</string></array>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/tatsuki-desk-wifilogin.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tatsuki-desk-wifilogin.log</string>
</dict></plist>
PLIST
launchctl bootstrap "gui/$UID_" "$AGENTS/$LABEL.plist"

echo "デスクトップに「Wi-Fi Login」を置きました。Wi-Fi が変わったときは自動で確認します。"
