#!/bin/bash
# Builds "Tatsuki's Desk.app" with the macOS tools only. Usage: bash desk/app/build.sh [install dir]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-$HOME/Applications}"
APP="$DEST/Tatsuki's Desk.app"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/deskapp.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

command -v swiftc >/dev/null || { echo "swiftc が見つかりません。xcode-select --install を実行してください。"; exit 1; }

mkdir -p "$WORK/App.app/Contents/MacOS" "$WORK/App.app/Contents/Resources"
swiftc -O -framework Cocoa -framework WebKit "$HERE/main.swift" -o "$WORK/App.app/Contents/MacOS/TatsukiDesk"

cat > "$WORK/App.app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Tatsuki's Desk</string>
  <key>CFBundleDisplayName</key><string>Tatsuki's Desk</string>
  <key>CFBundleIdentifier</key><string>com.tatsuki.desk.app</string>
  <key>CFBundleExecutable</key><string>TatsukiDesk</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST

# the icon is a nicety: the app works without it
if swift "$HERE/icon.swift" "$WORK/icon.png" 2>/dev/null; then
  SET="$WORK/AppIcon.iconset"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$WORK/icon.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z "$((s*2))" "$((s*2))" "$WORK/icon.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$WORK/App.app/Contents/Resources/AppIcon.icns" || true
fi

codesign --force --sign - "$WORK/App.app" 2>/dev/null || true
mkdir -p "$DEST"
rm -rf "$APP"
mv "$WORK/App.app" "$APP"
echo "$APP"
