"""Tests always get a throwaway Ken home.

Run from inside a live Ken (its shell has KEN_HOME set), setdefault kept the real home, and
the phone and job tests wiped its .env, jobs and setup. Loaded before any test imports bot.
"""
import os
import tempfile

os.environ["KEN_HOME"] = tempfile.mkdtemp(prefix="ken-test-")
for name in ("WORKSPACE", "TELEGRAM_BOT_TOKEN", "ALLOWED_USER_ID", "TELEGRAM_OWNER", "KEN_WEB_PORT",
             "KEN_OWN_LOGIN_ONLY", "KEN_POSTHOG_KEY", "KEN_ANALYTICS_ID", "CLAUDE_MODEL", "KEN_BRAIN", "CODEX_MODEL", "KEN_CODEX_HOME", "CODEX_BIN"):
    os.environ.pop(name, None)
os.environ["KEN_CRASH_REPORTS"] = os.environ["KEN_USAGE_STATS"] = "0"  # tests never send anything to the real PostHog project

# Tests never read the macOS Keychain. bot.get_oauth_token falls back to `security find-generic-password` for Claude
# Code's login, and each new test Python asked the owner's Mac for keychain access (a pop-up per run).
import subprocess  # noqa: E402

_run = subprocess.run


def _no_keychain(args, *a, **k):
    if isinstance(args, (list, tuple)) and args and str(args[0]) == "security":
        raise FileNotFoundError("tests never read the macOS keychain")
    return _run(args, *a, **k)


subprocess.run = _no_keychain
