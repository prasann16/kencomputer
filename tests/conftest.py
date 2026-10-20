"""Tests always get a throwaway Ken home.

Run from inside a live Ken (its shell has KEN_HOME set), setdefault kept the real home, and
the phone and job tests wiped its .env, jobs and setup. Loaded before any test imports bot.
"""
import os
import tempfile

os.environ["KEN_HOME"] = tempfile.mkdtemp(prefix="ken-test-")
for name in ("WORKSPACE", "TELEGRAM_BOT_TOKEN", "ALLOWED_USER_ID", "TELEGRAM_OWNER", "KEN_WEB_PORT",
             "KEN_OWN_LOGIN_ONLY", "KEN_POSTHOG_KEY", "CLAUDE_MODEL"):
    os.environ.pop(name, None)
os.environ["KEN_CRASH_REPORTS"] = os.environ["KEN_USAGE_STATS"] = "0"  # tests never send anything to the real PostHog project
