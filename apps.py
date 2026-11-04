"""Hosted Ken: the person's apps (Gmail, Slack, Notion and 1,700+ more), connected on their host's Apps page.

The host knows which apps are connected and makes one-tap Connect links; this computer only asks it, with the
same token it checks in with (KEN_KEEPALIVE_TOKEN), so no app or provider key is ever here. Ken runs
`ken --apps` (what's connected) and `ken --apps notion` (is Notion connected, and if not, the link to connect it).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

TIMEOUT = 15


def hosted() -> bool:
    return os.environ.get("KEN_KEEPALIVE_URL", "").startswith("https://") and bool(os.environ.get("KEN_KEEPALIVE_TOKEN"))


def _url(app: str = "") -> str:
    base = os.environ["KEN_KEEPALIVE_URL"].rsplit("/box/", 1)[0]
    return f"{base}/box/apps" + (f"?app={urllib.parse.quote(app)}" if app else "")


def fetch(app: str = "") -> dict:
    """{"connected": [{"slug", "name", "accounts": [...]}], "apps_url": ..., "match": {...} | None}."""
    req = urllib.request.Request(_url(app), headers={"Authorization": f"Bearer {os.environ['KEN_KEEPALIVE_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def describe(data: dict, app: str = "") -> str:
    """What `ken --apps` prints, for Ken to read."""
    connected = data.get("connected") or []
    lines = []
    if connected:
        lines.append("Connected apps: " + "; ".join(
            f"{a['name']} ({a['slug']})" + (f": {', '.join(a['accounts'])}" if a.get("accounts") else "") for a in connected))
    else:
        lines.append("No apps are connected yet.")
    if app:
        match = data.get("match")
        if not match:
            lines.append(f'No app called "{app}" is available. Tell them, and offer the Apps page: {data.get("apps_url", "")}')
        elif match.get("connected"):
            lines.append(f"{match['name']} is connected. Use the apps tools with it.")
        else:
            lines.append(f"{match['name']} isn't connected. Give them this one-tap link in one line, as is: "
                         f"{match['connect_url']}")
            lines.append("When they've connected it you'll be told; then carry on with what they asked.")
    lines.append(f"Every app: {data.get('apps_url', '')}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    app = " ".join(argv).strip()
    if not hosted():
        print("Apps are only on hosted Ken.")
        return 1
    try:
        print(describe(fetch(app), app))
        return 0
    except Exception:  # noqa: BLE001 - say it plainly; Ken tells them
        print("Couldn't check their apps just now. Try once more in a minute; if it still fails, tell them plainly.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
