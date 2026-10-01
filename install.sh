#!/bin/bash
# Ken installer — https://kencomputer.dev
#   curl -fsSL https://kencomputer.dev/install | bash
# Gives your AI its own computer: Claude Code + Telegram + memory, always on.
set -euo pipefail
umask 077

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
ask_secret() {
  printf '\033[36m%s\033[0m ' "$1" > /dev/tty
  IFS= read -r -s REPLY < /dev/tty
  printf '\n' > /dev/tty
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
if len(sys.argv) > 2 and sys.argv[2]:
    messages = [m for m in messages if m.get('text', '').strip() == sys.argv[2]
                and m.get('chat', {}).get('type') == 'private']
print(messages[-1]['from'][sys.argv[1]]) if messages else sys.exit(1)
" "$1" "${PAIRING_CODE:-}" 2>/dev/null
}

# Everything lives inside main() so bash parses the whole script before running
# any of it — otherwise, under `curl | bash`, any command that reads stdin
# (claude, pip, …) can swallow the rest of the script mid-flight.
main() {

say ""
say "  ken ●  — give your AI its own computer"
if [ "${KEN_AUTH_MODE:-subscription}" = api ]; then
  dim "  You'll need Telegram and your own Anthropic API key."
else
  dim "  You'll need Telegram and a Claude subscription."
fi
say ""

# ---------- 0. prerequisites ----------
case "$OS" in
  Darwin|Linux) ok "OS: $OS" ;;
  *) fail "Unsupported OS: $OS (macOS and Linux only)" ;;
esac
if [ "${KEN_HOSTED:-}" = "1" ] && [ "$(id -u)" = "0" ]; then
  fail "Run hosted onboarding as the ken user, not root."
fi
command -v git >/dev/null || fail "git is required — install it and re-run"
command -v curl >/dev/null || fail "curl is required"

# Every install runs the same Python, managed by uv (no sudo; it downloads Python itself).
# 3.13 is the newest the whole stack supports on both Intel and Apple Silicon Macs.
KEN_PYTHON=3.13
if ! command -v uv >/dev/null 2>&1; then
  say "→ Installing uv (manages Ken's Python, no sudo needed)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || fail "couldn't install uv — see https://docs.astral.sh/uv/ and re-run"
fi
ok "uv ready ($(uv --version))"

# ---------- 2. fetch ken ----------
mkdir -p "$KEN_HOME" "$BIN_DIR"
if [ -n "${KEN_SOURCE_DIR:-}" ]; then
  [ -f "$KEN_SOURCE_DIR/bot.py" ] || fail "KEN_SOURCE_DIR must contain Ken's source"
  [ "$(cd "$KEN_SOURCE_DIR" && pwd -P)" = "$(cd "$KEN_HOME/app" && pwd -P)" ] || fail "For bundled installs KEN_SOURCE_DIR must be $KEN_HOME/app"
  ok "Using the uploaded Ken release"
elif [ -d "$KEN_HOME/app/.git" ]; then
  git -C "$KEN_HOME/app" pull -q || true
  ok "Ken updated"
else
  git clone -q "$REPO" "$KEN_HOME/app"
  ok "Ken downloaded"
fi

# ---------- 3. python env ----------
# Rebuild the venv if it was made with a different Python than Ken uses now.
if [ -d "$KEN_HOME/venv" ] && ! "$KEN_HOME/venv/bin/python" -c "import sys; raise SystemExit(0 if sys.version.startswith('$KEN_PYTHON.') else 1)" 2>/dev/null; then
  dim "  (rebuilding environment on Python $KEN_PYTHON)"
  rm -rf "$KEN_HOME/venv"
fi
[ -d "$KEN_HOME/venv" ] || uv venv -q --seed --managed-python --python "$KEN_PYTHON" "$KEN_HOME/venv"
"$KEN_HOME/venv/bin/pip" install -q --upgrade pip
"$KEN_HOME/venv/bin/pip" install -q -U -r "$KEN_HOME/app/requirements.txt"
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
    ask_secret "Paste your bot token:"
    BOT_TOKEN="$REPLY"
    BOT_INFO="$(tg getMe || true)"
    BOT_USER="$(printf '%s' "$BOT_INFO" | bot_username || true)"
    if [ -n "${BOT_USER:-}" ]; then ok "Connected to @$BOT_USER"; break; fi
    printf "  \033[31mThat token didn't work — try again.\033[0m\n" > /dev/tty
  done

  tg deleteWebhook >/dev/null || true
  say ""
  say "── Step 2 of 2: introduce yourself ──"
  PAIRING_CODE="$(python3 -c 'import secrets; print("ken-" + secrets.token_hex(8))')"
  dim "  Open @$BOT_USER in Telegram — https://t.me/$BOT_USER — and send this code:"
  say "  $PAIRING_CODE"
  dim "  This links your private Telegram account. I'll wait up to 15 minutes."
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
API_KEY="${ANTHROPIC_API_KEY:-}"
if [ "${KEN_AUTH_MODE:-subscription}" = "api" ]; then
  if [ -z "$API_KEY" ]; then
    ask_secret "Paste your own Anthropic API key (billed to your account):"
    API_KEY="$REPLY"
  fi
  [ -n "$API_KEY" ] || fail "An API key is required"
  # Do not inherit a subscription token when explicitly selecting API billing.
  if ! env -u CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY="$API_KEY" \
      "$CLAUDE_BIN" -p "Reply with exactly OK" --model haiku </dev/null >/dev/null 2>&1; then
    fail "Claude API check failed; check the key, account credit, and network"
  fi
  ok "Claude API connected"
elif "$CLAUDE_BIN" -p "Reply with exactly OK" --model haiku </dev/null >/dev/null 2>&1; then
  if [ -f "$KEN_HOME/.env" ]; then
    OAUTH_TOKEN="$(sed -n 's/^CLAUDE_CODE_OAUTH_TOKEN=//p' "$KEN_HOME/.env")"
  fi
  ok "Claude is already signed in on this machine"
else
  dim "  Ken runs on your Claude subscription. We'll create a long-lived token."
  dim "  A browser window will open — approve, then paste the code back here."
  say ""
  TOKEN_OUTPUT="$(mktemp)"
  "$CLAUDE_BIN" setup-token < /dev/tty > "$TOKEN_OUTPUT" 2>&1 || true
  OAUTH_TOKEN="$(grep -oE 'sk-ant-oat[A-Za-z0-9_-]+' "$TOKEN_OUTPUT" | tail -1 || true)"
  rm -f "$TOKEN_OUTPUT"
  if [ -z "$OAUTH_TOKEN" ]; then
    ask_secret "Paste the token (starts with sk-ant-oat…):"
    OAUTH_TOKEN="$REPLY"
  fi
  [ -n "$OAUTH_TOKEN" ] || fail "No Claude token — run 'claude setup-token' and re-run the installer"
  ok "Claude connected"
fi

# ---------- 6. config + memory ----------
mkdir -p "$KEN_HOME/work" "$KEN_HOME/memory"
cat > "$KEN_HOME/.env" <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
ALLOWED_USER_ID=$USER_ID
CLAUDE_CODE_OAUTH_TOKEN=$OAUTH_TOKEN
ANTHROPIC_API_KEY=$API_KEY
CLAUDE_MODEL=${KEN_INITIAL_MODEL:-}
WORKSPACE=$KEN_HOME/work
WHISPER_MODEL=small
TASK_TIMEOUT_SECONDS=1800
EOF
if [ "${KEN_HOSTED:-}" = "1" ]; then
  printf '\nKEN_NO_AUTOUPDATE=1\n' >> "$KEN_HOME/.env"
  "$KEN_HOME/venv/bin/pip" freeze > "$KEN_HOME/app/requirements-deployed.txt"
  "$CLAUDE_BIN" --version > "$KEN_HOME/app/claude-deployed-version.txt"
fi
chmod 600 "$KEN_HOME/.env"

# migrate older installs: CLAUDE.md -> SOUL.md
if [ -f "$KEN_HOME/work/CLAUDE.md" ] && [ ! -f "$KEN_HOME/work/SOUL.md" ]; then
  mv "$KEN_HOME/work/CLAUDE.md" "$KEN_HOME/work/SOUL.md"
fi
if [ ! -f "$KEN_HOME/work/SOUL.md" ]; then
  sed "s/{{NAME}}/${FIRST_NAME:-my human}/g" "$KEN_HOME/app/SOUL.template.md" > "$KEN_HOME/work/SOUL.md"
fi

# Standing jobs: every install gets a morning brief and the nightly memory review.
if [ ! -f "$KEN_HOME/jobs.json" ]; then
  cp "$KEN_HOME/app/jobs.default.json" "$KEN_HOME/jobs.json"
fi

# Hosting note: the assistant answers "who can see my data" from this file, never from assumption.
if [ ! -f "$KEN_HOME/hosting.md" ]; then
  if [ "${KEN_HOSTED:-}" = "1" ]; then
    cat > "$KEN_HOME/hosting.md" <<'EOF'
# This machine: a dedicated server provisioned by kencomputer.dev

- Provisioned and handed over by the kencomputer.dev operator. Same open-source Ken software as a self-install; only who racked the box differs.
- Everything of theirs lives here: SOUL.md, memory, history, files, credentials. Voice notes are transcribed on this machine.
- What leaves this machine: calls to the Claude API (Anthropic) to run you, billed to their own Anthropic API key, and whatever a connected service is told to fetch or send.
- Operator access: the operator keeps a root SSH login to this server for setup, health checks, updates, and backups. That login can read everything on the machine. Say so plainly if asked who can see their data.
- Backups: not automatic. The operator can take an on-demand backup, which copies the whole Ken home directory, credentials included, to the operator's computer. Whether the hosting provider snapshots the VM depends on what was chosen at provisioning.
- Hosted vs self-hosted, if they ask: hosted is zero setup and always on but trusts the operator; self-hosted runs on hardware they control but they own uptime, patching, and backups.
EOF
  else
    cat > "$KEN_HOME/hosting.md" <<'EOF'
# This machine: self-hosted

- The human installed Ken themselves, on a machine they control, with the open-source install script. Nobody at kencomputer.dev has any access to it.
- Everything lives here: SOUL.md, memory, history, files, credentials. Voice notes are transcribed on this machine.
- What leaves this machine: calls to the Claude API (Anthropic) to run you, on their own Claude subscription or API key, and whatever a connected service is told to fetch or send.
- Backups: nothing automatic. If this machine dies, ~/.ken is gone unless they back it up themselves. Say so if asked.
EOF
  fi
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
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then
      ok "Running as a systemd service (survives reboots)"
    else
      [ "${KEN_HOSTED:-}" != "1" ] || fail "Hosted install requires lingering: sudo loginctl enable-linger $USER"
      ok "Running as a systemd service"
      dim "  Couldn't enable lingering, so ken stops when you log out and won't restart on reboot."
      dim "  Fix once with: sudo loginctl enable-linger $USER"
    fi
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
if [ "$OS" = "Linux" ] && [ "${KEN_HOSTED:-}" = "1" ]; then
  systemctl --user is-active --quiet ken || fail "Ken did not stay running; inspect ken logs"
fi
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
