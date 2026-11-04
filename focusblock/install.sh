#!/bin/bash
# FocusBlock をインストールして常駐させる（管理者パスワードが必要）
set -euo pipefail
if [ "$EUID" -ne 0 ]; then exec sudo "$0" "$@"; fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="/Library/Application Support/FocusBlock"
PLIST="/Library/LaunchDaemons/com.focusblock.daemon.plist"
PY=/usr/bin/python3

if ! "$PY" -c 'import sys; assert sys.version_info >= (3, 8)' 2>/dev/null; then
  echo "python3 が使えません。ターミナルで xcode-select --install を実行してから再度お試しください。"
  exit 1
fi

mkdir -p "$DEST"
install -m 755 "$SRC/daemon.py" "$DEST/daemon.py"
install -m 644 "$SRC/ui.html" "$DEST/ui.html"
install -m 755 "$SRC/uninstall.sh" "$DEST/uninstall.sh"
install -m 755 "$SRC/focusctl.py" "$DEST/focusctl.py"
chown -R root:wheel "$DEST"
chmod 755 "$DEST"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.focusblock.daemon</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DEST/daemon.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/Library/Logs/FocusBlock.log</string>
</dict>
</plist>
EOF
chown root:wheel "$PLIST"
chmod 644 "$PLIST"

launchctl bootout system "$PLIST" 2>/dev/null || true
launchctl bootstrap system "$PLIST"

# アプリケーションフォルダに、解除と設定画面のためのランチャーを置く
rm -rf /Applications/FocusBlock.app
osacompile -o /Applications/FocusBlock.app "$SRC/launcher.applescript"

echo "インストール完了。/Applications/FocusBlock.app から設定画面を開けます。"
sleep 1
if [ -n "${SUDO_USER:-}" ]; then sudo -u "$SUDO_USER" open "http://127.0.0.1:47800" || true; fi
