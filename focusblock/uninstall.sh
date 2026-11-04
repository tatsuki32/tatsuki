#!/bin/bash
# FocusBlock を完全に削除し、プロキシ設定を元に戻す
set -uo pipefail
if [ "$EUID" -ne 0 ]; then exec sudo "$0" "$@"; fi

DEST="/Library/Application Support/FocusBlock"
PLIST="/Library/LaunchDaemons/com.focusblock.daemon.plist"

launchctl bootout system "$PLIST" 2>/dev/null || true
if [ -f "$DEST/daemon.py" ]; then /usr/bin/python3 "$DEST/daemon.py" --restore-proxy || true; fi
rm -f "$PLIST"
rm -rf "$DEST" /Applications/FocusBlock.app
echo "FocusBlock を削除しました。"
