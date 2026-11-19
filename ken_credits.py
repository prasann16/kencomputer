"""`ken --credits`: what used their Ken AI credits this month, by day, for when they ask Ken "what used my credits?".

Hosted Ken only: the host keeps the list (ken-hosted /box/credits, the same one Settings → Plan shows) and this
computer asks with its Ken AI credits token (KEN_INCLUDED_AI_URL/TOKEN). Prints plain lines, newest day first.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def credits_url() -> tuple[str, str] | None:
    ai = os.environ.get("KEN_INCLUDED_AI_URL", "").strip().rstrip("/")
    token = os.environ.get("KEN_INCLUDED_AI_TOKEN", "").strip()
    if not token or not ai.endswith("/ai") or not ai.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return None
    return ai[: -len("/ai")] + "/box/credits", token


def report(usage: dict) -> str:
    """The list as lines: "Today · Dub · track club reel (Portuguese) · $0.55"."""
    days = usage.get("days") or []
    if not days:
        return "Nothing has used Ken AI credits this month yet."
    lines = [f"Ken AI credits used this month: {usage.get('total', '')}"]
    for day in days:
        for e in day.get("entries") or []:
            lines.append(f"{day.get('label', day.get('day', ''))} · {e.get('label', '')} · {e.get('usd', '')}")
    return "\n".join(lines)


def main() -> int:
    where = credits_url()
    if not where:
        print("Ken AI credits are only on hosted Ken; this computer has no list of them.")
        return 1
    req = urllib.request.Request(where[0], headers={"Authorization": f"Bearer {where[1]}", "User-Agent": "Ken"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            usage = json.loads(r.read() or b"{}")
    except (urllib.error.URLError, ValueError, OSError):
        print("Couldn't get the Ken AI credits list just now. They can see it in Settings → Plan.")
        return 1
    print(report(usage))
    return 0
