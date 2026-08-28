#!/bin/bash
# Ken installer — https://kencomputer.dev
#   curl -fsSL https://kencomputer.dev/install | bash
# Gives your AI its own computer: Claude Code + Telegram + memory, always on.
set -euo pipefail

REPO="${KEN_REPO:-https://github.com/prasann16/kencomputer.git}"
KEN_HOME="${KEN_HOME:-$HOME/.ken}"
BIN_DIR="$HOME/.local/bin"
OS="$(uname -s)"

# ---------- helpers ----------
say()  { printf "\033[1m%s\033[0m\n" "$*"; }
dim()  { printf "\033[2m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✗ %s\033[0m\n" "$*"; exit 1; }
ask()  { # ask "prompt" -> $REPLY  (reads from the terminal even under curl|bash)
  printf "\033[36m%s\033[0m " "$1" > /dev/tty
  IFS= read -r REPLY < /dev/tty
}

tg() { curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/$1" "${@:2}"; }

# Pull one field out of a Telegram API response. Plain lookups, no eval.
bot_username() {
  python3 -c "import json,sys; print(json.load(sys.stdin)['result']['username'])" 2>/dev/null
}
last_sender() { # $1: "id" or "first_name"
  python3 -c "
import json, sys
updates = json.load(sys.stdin)['result']
messages = [u['message'] for u in updates if 'message' in u]
print(messages[-1]['from'][sys.argv[1]]) if messages else sys.exit(1)
" "$1" 2>/dev/null
}

# Everything lives inside main() so bash parses the whole script before running
# any of it — otherwise, under `curl | bash`, any command that reads stdin
# (claude, pip, …) can swallow the rest of the script mid-flight.
main() {

say ""
say "  ken ●  — give your AI its own computer"
dim "  ~2 minutes. You'll need: the Telegram app, and a Claude subscription."
say ""

# ---------- 0. prerequisites ----------
case "$OS" in
  Darwin|Linux) ok "OS: $OS" ;;
  *) fail "Unsupported OS: $OS (macOS and Linux only)" ;;
esac
command -v git >/dev/null || fail "git is required — install it and re-run"
command -v curl >/dev/null || fail "curl is required"

# Find a Python >= 3.9 — people's default python3 is often ancient (conda, old distros).
find_python() {
  for p in python3.13 python3.12 python3.11 python3.10 python3.9 python3 \
           /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$p" >/dev/null 2>&1; then
      if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
        command -v "$p"; return 0
      fi
    fi
  done
  return 1
}
PY="$(find_python || true)"
if [ -n "$PY" ]; then
  ok "Python found: $PY ($("$PY" -V 2>&1))"
else
  say "→ No modern Python found — installing a private one (via uv, no sudo needed)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || fail "couldn't install uv — install Python 3.10+ manually and re-run"
  ok "uv installed (manages its own Python)"
  PY="uv"
fi
ok "git, curl found"

# ---------- 1. claude code ----------
if command -v claude >/dev/null || [ -x "$HOME/.local/bin/claude" ]; then
  ok "Claude Code found"
else
  say "→ Installing Claude Code (Anthropic's official installer)…"
  curl -fsSL https://claude.ai/install.sh | bash
  ok "Claude Code installed"
fi
export PATH="$HOME/.local/bin:$PATH"
CLAUDE_BIN="$(command -v claude || echo "$HOME/.local/bin/claude")"

# ---------- 2. fetch ken ----------
mkdir -p "$KEN_HOME" "$BIN_DIR"
if [ -d "$KEN_HOME/app/.git" ]; then
  git -C "$KEN_HOME/app" pull -q || true
  ok "Ken updated"
else
  git clone -q "$REPO" "$KEN_HOME/app"
  ok "Ken downloaded"
fi

# ---------- 3. python env ----------
# Rebuild the venv if it exists but was made with a too-old Python.
if [ -d "$KEN_HOME/venv" ]; then
  if ! "$KEN_HOME/venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
    dim "  (rebuilding environment — previous one used an old Python)"
    rm -rf "$KEN_HOME/venv"
  fi
fi
if [ ! -d "$KEN_HOME/venv" ]; then
  if [ "$PY" = "uv" ]; then
    uv venv --seed --python 3.12 "$KEN_HOME/venv" >/dev/null
  else
    "$PY" -m venv "$KEN_HOME/venv"
  fi
fi
"$KEN_HOME/venv/bin/pip" install -q --upgrade pip
"$KEN_HOME/venv/bin/pip" install -q -r "$KEN_HOME/app/requirements.txt"
ok "Python environment ready"

if [ ! -d "$HOME/.cache/huggingface/hub/models--Systran--faster-whisper-small" ]; then
  dim "  Fetching the voice-transcription model (one-time, ~460MB — hang tight)…"
  "$KEN_HOME/venv/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')" </dev/null >/dev/null 2>&1 || true
fi
ok "Voice transcription ready"

# ---------- 4. telegram bot ----------
if [ -f "$KEN_HOME/.env" ] && grep -q "^TELEGRAM_BOT_TOKEN=." "$KEN_HOME/.env"; then
  ok "Telegram already configured (delete ~/.ken/.env to redo)"
  BOT_TOKEN="$(grep '^TELEGRAM_BOT_TOKEN=' "$KEN_HOME/.env" | cut -d= -f2-)"
  USER_ID="$(grep '^ALLOWED_USER_ID=' "$KEN_HOME/.env" | cut -d= -f2-)"
  FIRST_NAME="there"
else
  say ""
  say "── Step 1 of 2: create your bot (1 minute) ──"
  dim "  1. Open Telegram (phone is fine) and message @BotFather"
  dim "  2. Send:  /newbot   — pick any name (e.g. Ken), any username"
  dim "  3. BotFather replies with a token like 123456:ABC-xyz…"
  say ""
  while true; do
    ask "Paste your bot token:"
    BOT_TOKEN="$REPLY"
    BOT_INFO="$(tg getMe || true)"
    BOT_USER="$(printf '%s' "$BOT_INFO" | bot_username || true)"
    if [ -n "${BOT_USER:-}" ]; then ok "Connected to @$BOT_USER"; break; fi
    printf "  \033[31mThat token didn't work — try again.\033[0m\n" > /dev/tty
  done

  tg deleteWebhook >/dev/null || true
  say ""
  say "── Step 2 of 2: introduce yourself ──"
  dim "  Open @$BOT_USER in Telegram — https://t.me/$BOT_USER — and send it"
  dim "  any message (a 👋 works). Take your time; I'll wait up to 15 minutes."
  printf "  waiting for your message to @%s " "$BOT_USER" > /dev/tty
  USER_ID=""; FIRST_NAME=""
  for _ in $(seq 1 450); do
    UPD="$(tg "getUpdates?timeout=2" || true)"
    USER_ID="$(printf '%s' "$UPD" | last_sender id || true)"
    FIRST_NAME="$(printf '%s' "$UPD" | last_sender first_name || true)"
    [ -n "$USER_ID" ] && break
    printf "." > /dev/tty
    sleep 2
  done
  printf "\n" > /dev/tty
  if [ -z "$USER_ID" ]; then
    printf "\n  \033[31m✗ SETUP DID NOT FINISH — no message arrived.\033[0m\n" > /dev/tty
    printf "  \033[31m  Re-run the installer and message the bot when asked:\033[0m\n" > /dev/tty
    printf "  \033[31m  curl -fsSL kencomputer.dev/install | bash\033[0m\n\n" > /dev/tty
    exit 1
  fi
  ok "Hi ${FIRST_NAME:-there}! Locked to your Telegram account ($USER_ID)"
fi

# ---------- 5. claude auth ----------
say ""
say "── Connecting Claude ──"
OAUTH_TOKEN=""
if "$CLAUDE_BIN" -p "Reply with exactly OK" --model haiku </dev/null >/dev/null 2>&1; then
  ok "Claude is already signed in on this machine"
else
  dim "  Ken runs on your Claude subscription. We'll create a long-lived token."
  dim "  A browser window will open — approve, then paste the code back here."
  say ""
  "$CLAUDE_BIN" setup-token < /dev/tty > /tmp/ken-token-out 2>&1 || true
  OAUTH_TOKEN="$(grep -oE 'sk-ant-oat[A-Za-z0-9_-]+' /tmp/ken-token-out | tail -1 || true)"
  rm -f /tmp/ken-token-out
  if [ -z "$OAUTH_TOKEN" ]; then
    ask "Paste the token (starts with sk-ant-oat…):"
    OAUTH_TOKEN="$REPLY"
  fi
  [ -n "$OAUTH_TOKEN" ] || fail "No Claude token — run 'claude setup-token' and re-run the installer"
  ok "Claude connected"
fi

# ---------- 6. config + memory ----------
mkdir -p "$KEN_HOME/work"
cat > "$KEN_HOME/.env" <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
ALLOWED_USER_ID=$USER_ID
CLAUDE_CODE_OAUTH_TOKEN=$OAUTH_TOKEN
CLAUDE_MODEL=claude-sonnet-5
WORKSPACE=$KEN_HOME/work
WHISPER_MODEL=small
TASK_TIMEOUT_SECONDS=1800
EOF
chmod 600 "$KEN_HOME/.env"

# migrate older installs: CLAUDE.md -> SOUL.md
if [ -f "$KEN_HOME/work/CLAUDE.md" ] && [ ! -f "$KEN_HOME/work/SOUL.md" ]; then
  mv "$KEN_HOME/work/CLAUDE.md" "$KEN_HOME/work/SOUL.md"
fi
if [ ! -f "$KEN_HOME/work/SOUL.md" ]; then
  sed "s/{{NAME}}/${FIRST_NAME:-my human}/g" "$KEN_HOME/app/SOUL.template.md" > "$KEN_HOME/work/SOUL.md"
fi
ok "Config and memory written to ~/.ken"

# ---------- 7. ken CLI ----------
cp "$KEN_HOME/app/ken" "$BIN_DIR/ken"
chmod +x "$BIN_DIR/ken"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) dim "  (add $BIN_DIR to your PATH to use the 'ken' command)" ;;
esac

# ---------- 8. service ----------
if [ "$OS" = "Linux" ]; then
  if command -v systemctl >/dev/null; then
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    sed -e "s|{{KEN_HOME}}|$KEN_HOME|g" \
      "$KEN_HOME/app/ken.service.template" > "$UNIT_DIR/ken.service"
    systemctl --user daemon-reload
    systemctl --user enable --now ken >/dev/null 2>&1
    loginctl enable-linger "$USER" >/dev/null 2>&1 || true
    ok "Running as a systemd service (survives reboots)"
  else
    fail "systemd not found — start manually: ken start"
  fi
else
  PLIST="$HOME/Library/LaunchAgents/dev.kencomputer.ken.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$KEN_HOME/logs"
  sed -e "s|{{KEN_HOME}}|$KEN_HOME|g" \
    "$KEN_HOME/app/ken.plist.template" > "$PLIST"
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load -w "$PLIST"
  ok "Running as a background service (starts at login)"
fi

sleep 3
say ""
say "  ● It's alive."
say ""
dim "  Open Telegram — your assistant is waking up for the first time."
dim "  Say hello. It has a question for you."
dim ""
dim "  Manage it:  ken status · ken logs · ken update · ken restart"
dim "  Its soul: $KEN_HOME/work/SOUL.md  (or just tell it to remember things)"
say ""

}
main
