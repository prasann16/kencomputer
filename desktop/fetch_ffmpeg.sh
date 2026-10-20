#!/bin/bash
# ffmpeg for Ken for Mac, one per arch, so Ken can cut clips and burn in captions on any Mac.
# Static builds from ffmpeg-static (GPL; their license and build notes ship alongside), pinned
# by checksum. Lands in dist/ffmpeg-<arch>/, which electron-builder copies into the app.
#   desktop/fetch_ffmpeg.sh [arm64|x64 ...]   (default: both)
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=b6.1.1
BASE="https://github.com/eugeneware/ffmpeg-static/releases/download/$TAG"
sha() { case "$1" in
  arm64) echo 8923876afa8db5585022d7860ec7e589af192f441c56793971276d450ed3bbfa ;;
  x64)   echo 929b375c1182d956c51f7ac25e0b2b0411fb01f6f407aa15c9758efeb4242106 ;;
esac; }
for arch in "${@:-arm64 x64}"; do
  for a in $arch; do
    out="dist/ffmpeg-$a"
    mkdir -p "$out"
    curl -fsSL "$BASE/ffmpeg-darwin-$a.gz" -o "$out/ffmpeg.gz"
    echo "$(sha "$a")  $out/ffmpeg.gz" | shasum -a 256 -c - >/dev/null
    gunzip -f "$out/ffmpeg.gz" && chmod 755 "$out/ffmpeg"
    curl -fsSL "$BASE/darwin-$a.LICENSE" -o "$out/LICENSE"
    curl -fsSL "$BASE/darwin-$a.README" -o "$out/README"
    echo "ffmpeg $TAG for $a: $(du -h "$out/ffmpeg" | cut -f1)"
  done
done
