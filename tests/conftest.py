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
# No test may open a real browser. On a desktop with a screen the sign-in helper hands its link to xdg-open, so
# test_the_link_is_the_one_the_browser_opened opened its fake Claude sign-in link in the developer's own browser
# (seen as "claude.ai/oauth/authorize?code=true&redirect_uri=…localhost:5…", a link Claude rejects).
for name in ("DISPLAY", "WAYLAND_DISPLAY", "BROWSER"):
    os.environ.pop(name, None)
os.environ["KEN_CRASH_REPORTS"] = os.environ["KEN_USAGE_STATS"] = "0"  # tests never send anything to the real PostHog project
