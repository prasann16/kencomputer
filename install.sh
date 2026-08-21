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

json_get() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1" 2>/dev/null; }

say ""
say "  ken ●  — give your AI its own computer"
dim "  ~2 minutes. You'll need: the Telegram app, and a Claude subscription."
say ""

# ---------- 0. prerequisites ----------
case "$OS" in
  Darwin|Linux) ok "OS: $OS" ;;
  *) fail "Unsupported OS: $OS (macOS and Linux only)" ;;
esac
command -v python3 >/dev/null || fail "python3 is required — install it and re-run"
command -v git >/dev/null || fail "git is required — install it and re-run"
command -v curl >/dev/null || fail "curl is required"
ok "python3, git, curl found"

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
if [ ! -d "$KEN_HOME/venv" ]; then
  python3 -m venv "$KEN_HOME/venv"
fi
"$KEN_HOME/venv/bin/pip" install -q --upgrade pip
"$KEN_HOME/venv/bin/pip" install -q -r "$KEN_HOME/app/requirements.txt"
ok "Python environment ready"

# ---------- 4. telegram bot ----------
if [ -f "$KEN_HOME/.env" ] && grep -q "^TELEGRAM_BOT_TOKEN=." "$KEN_HOME/.env"; then
  ok "Telegram already configured (delete ~/.ken/.env to redo)"
  BOT_TOKEN="$(grep '^TELEGRAM_BOT_TOKEN=' "$KEN_HOME/.env" | cut -d= -f2-)"
  USER_ID="$(grep '^ALLOWED_USER_ID=' "$KEN_HOME/.env" | cut -d= -f2-)"
  FIRST_NAME="there"
else
  say ""
  say "── Step 1 of 2: create your bot (1 minute) ──"
  dim "  1. Open Telegram and message @BotFather"
  dim "  2. Send:  /newbot   — pick any name (e.g. Ken), any username"
  dim "  3. BotFather replies with a token like 123456:ABC-xyz…"
  say ""
  while true; do
    ask "Paste your bot token:"
    BOT_TOKEN="$REPLY"
    BOT_INFO="$(tg getMe || true)"
    BOT_USER="$(printf '%s' "$BOT_INFO" | json_get "d['result']['username']" || true)"
    if [ -n "${BOT_USER:-}" ]; then ok "Connected to @$BOT_USER"; break; fi
    printf "  \033[31mThat token didn't work — try again.\033[0m\n" > /dev/tty
  done

  tg deleteWebhook >/dev/null || true
  say ""
  say "── Step 2 of 2: introduce yourself ──"
  dim "  Open @$BOT_USER in Telegram and send it any message (a 👋 works)."
  printf "  waiting" > /dev/tty
  USER_ID=""; FIRST_NAME=""
  OFFSET=0
  for _ in $(seq 1 120); do
    UPD="$(tg "getUpdates?timeout=2&offset=$OFFSET" || true)"
    USER_ID="$(printf '%s' "$UPD" | json_get "d['result'][-1]['message']['from']['id']" || true)"
    FIRST_NAME="$(printf '%s' "$UPD" | json_get "d['result'][-1]['message']['from']['first_name']" || true)"
    [ -n "$USER_ID" ] && break
    printf "." > /dev/tty
    sleep 2
  done
  printf "\n" > /dev/tty
  [ -n "$USER_ID" ] || fail "Didn't see a message. Re-run the installer and message the bot when asked."
  ok "Hi ${FIRST_NAME:-there}! Locked to your Telegram account ($USER_ID)"
fi

# ---------- 5. claude auth ----------
say ""
say "── Connecting Claude ──"
OAUTH_TOKEN=""
if "$CLAUDE_BIN" -p "Reply with exactly OK" --model haiku >/dev/null 2>&1; then
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

if [ ! -f "$KEN_HOME/work/CLAUDE.md" ]; then
  sed "s/{{NAME}}/${FIRST_NAME:-my human}/g" "$KEN_HOME/app/CLAUDE.template.md" > "$KEN_HOME/work/CLAUDE.md"
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
say "  ● Ken is alive."
say ""
dim "  Open Telegram and talk to your bot — text or voice notes."
dim "  Try: \"what computer are you running on?\""
dim ""
dim "  Manage it:  ken status · ken logs · ken update · ken restart"
dim "  Its memory: $KEN_HOME/work/CLAUDE.md  (or just tell it to remember things)"
say ""
