#!/bin/bash
# Check a draft release the way a new user would get it, before publishing:
#   desktop/check-release.sh v0.1.2            # check only
#   desktop/check-release.sh v0.1.2 --publish  # publish if every check passes
# Downloads this Mac's .dmg, checks macOS accepts it, then runs its engine in a
# throwaway home and sends a real chat message and a real (spoken) voice note.
set -uo pipefail
TAG="${1:?usage: desktop/check-release.sh vX.Y.Z [--publish]}"
REPO=prasann16/kencomputer
PORT=7799
WORK="$(mktemp -d)"
FAILED=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
cleanup() {
  [ -n "${ENGINE:-}" ] && { kill "$ENGINE"; wait "$ENGINE"; } 2>/dev/null
  [ -n "${MOUNT:-}" ] && { hdiutil detach -quiet "$MOUNT" || hdiutil detach -quiet -force "$MOUNT"; } 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null && { echo "Port $PORT is in use; stop whatever is on it and re-run."; exit 1; }
VERSION="${TAG#v}"
[ "$(uname -m)" = arm64 ] && DMG="Ken-arm64.dmg" || DMG="Ken-x64.dmg"
echo "Checking $TAG ($DMG)"

# 1. Download it like a browser would (quarantined), and let macOS judge it.
gh release download "$TAG" -R "$REPO" -p "$DMG" -D "$WORK" || { fail "download $DMG"; exit 1; }
xattr -w com.apple.quarantine "0081;$(printf %x "$(date +%s)");Safari;" "$WORK/$DMG"
MOUNT="$(hdiutil attach -nobrowse -readonly "$WORK/$DMG" | tail -1 | awk -F'\t' '{print $NF}')"
APP="$MOUNT/Ken.app"
[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")" = "$VERSION" ] \
  && pass "version $VERSION" || fail "app version is not $VERSION"
GATEKEEPER="$(spctl -a -vv -t exec "$APP" 2>&1)"
case "$GATEKEEPER" in *accepted*"Notarized Developer ID"*) pass "macOS accepts it (signed, notarized)" ;;
  *) fail "Gatekeeper: $GATEKEEPER" ;; esac

# 2. Start the bundled engine in a throwaway home, with the env the app gives it.
HOME_DIR="$WORK/home"; mkdir -p "$HOME_DIR"
# `exec` so $! is the engine itself and cleanup really stops it.
(cd /tmp && KEN_HOME="$HOME_DIR" KEN_APP_VERSION="$VERSION" TELEGRAM_BOT_TOKEN= KEN_WEB_PORT=$PORT \
  KEN_NODE="$APP/Contents/MacOS/Ken" \
  KEN_BROWSER_MCP="$APP/Contents/Resources/browser/chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js" \
  exec "$APP/Contents/Resources/engine/ken-engine" > "$WORK/engine.log" 2>&1) &
ENGINE=$!
for _ in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 1; done
AUTH="Authorization: Bearer $(cat "$HOME_DIR/web-token" 2>/dev/null)"
api() { curl -s -m 300 -H "$AUTH" "$@"; }
api "http://127.0.0.1:$PORT/api/meta" | grep -q "\"rev\": \"$VERSION\"" && pass "engine starts" || fail "engine didn't start (see below)"

# 3. A real chat message.
api -H 'Content-Type: application/json' -d '{"text":"Reply with exactly: ok"}' "http://127.0.0.1:$PORT/api/chats/home/messages" >/dev/null
REPLY=""
for _ in $(seq 1 90); do
  REPLY="$(api "http://127.0.0.1:$PORT/api/chats/home" | python3 -c 'import json,sys; m=[x["text"] for x in json.load(sys.stdin)["messages"] if x["role"]=="ken"]; print(m[-1] if m else "")' 2>/dev/null)"
  [ -n "$REPLY" ] && break; sleep 2
done
[ "$(echo "$REPLY" | tr -d '[:space:].' | tr 'A-Z' 'a-z')" = ok ] && pass "chat: Ken replied" || fail "chat: got \"${REPLY:-no reply}\""

# 4. A real voice note, spoken by the Mac's own voice.
# A named built-in voice: the default voice can return silence while macOS swaps it.
say -v Samantha -o "$WORK/voice.aiff" "Hello Ken, how are you today?"
afinfo "$WORK/voice.aiff" 2>/dev/null | grep -qE 'estimated duration: [1-9]' \
  || { fail "test audio: macOS 'say' produced no speech, so voice wasn't tested"; }
TEXT="$(api --data-binary @"$WORK/voice.aiff" "http://127.0.0.1:$PORT/api/voice")"
echo "$TEXT" | grep -qi hello && pass "voice: heard $(echo "$TEXT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("text",""))')" \
  || fail "voice: $TEXT"

if [ "$FAILED" = 1 ]; then
  echo; echo "Engine log errors:"; grep -iE "error|traceback|dlopen" "$WORK/engine.log" | head -10
  echo; echo "Not publishing $TAG."; exit 1
fi
echo; echo "All checks passed."
if [ "${2:-}" = --publish ]; then
  gh release edit "$TAG" -R "$REPO" --draft=false --latest >/dev/null && echo "Published $TAG."
else
  echo "Publish with: desktop/check-release.sh $TAG --publish"
fi
