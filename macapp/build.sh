#!/bin/bash
# Builds dist/Ken.app — a universal (Apple silicon + Intel) menu bar app.
# Needs only the Xcode Command Line Tools (xcode-select --install).
#
#   ./build.sh                       ad-hoc signed: runs on this Mac, and on
#                                    others after a right-click → Open
#   CODESIGN_IDENTITY="Developer ID Application: …" ./build.sh
#                                    then notarize (see README.md) to ship
set -euo pipefail
cd "$(dirname "$0")"

APP=dist/Ken.app
rm -rf dist
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "→ compiling"
for arch in arm64 x86_64; do
  swiftc -O -swift-version 5 -parse-as-library \
    -target "$arch-apple-macos13.0" \
    -o "dist/Ken-$arch" KenApp.swift
done
lipo -create -output "$APP/Contents/MacOS/Ken" dist/Ken-arm64 dist/Ken-x86_64
rm -f dist/Ken-arm64 dist/Ken-x86_64

echo "→ icon"
swiftc -O -o dist/gen-icon gen-icon.swift
dist/gen-icon dist/AppIcon.iconset
iconutil -c icns -o "$APP/Contents/Resources/AppIcon.icns" dist/AppIcon.iconset
rm -rf dist/gen-icon dist/AppIcon.iconset

cp Info.plist "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

echo "→ signing (${CODESIGN_IDENTITY:-ad-hoc})"
codesign --force --deep --options runtime --sign "${CODESIGN_IDENTITY:--}" "$APP"

echo "✓ $APP"
