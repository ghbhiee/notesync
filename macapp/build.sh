#!/bin/bash
# Build + bundle + sign NoteSyncBar (pattern from hypervibe/create_app_bundle.sh)
set -e
cd "$(dirname "$0")"
APP=NoteSyncBar
BUNDLE_ID="${BUNDLE_ID:-com.notesync.bar}"
CODESIGN_ID="${CODESIGN_ID:--}"
SDK=$(xcrun --show-sdk-path --sdk macosx)
ARCH=$(uname -m)
swiftc -sdk "$SDK" -target "$ARCH-apple-macosx13.0" -O -o "$APP" NoteSyncBar.swift \
  -framework AppKit -framework ServiceManagement -framework WebKit
rm -rf "$APP.app"
mkdir -p "$APP.app/Contents/MacOS"
cp "$APP" "$APP.app/Contents/MacOS/$APP"
cat > "$APP.app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>$APP</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleName</key><string>NoteSync</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>NoteSync</string>
</dict>
</plist>
PLIST
codesign --force --sign "$CODESIGN_ID" "$APP.app"
echo "built + signed: $APP.app"
