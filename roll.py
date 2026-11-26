"""Their camera roll, without uploading it (hosted Ken, where the site switches it on).

The phone sends the site an outline of everything on it: when each photo or video was taken, which kind, how long.
Ken looks at that by day, picks the clips a task needs, and asks for them; the phone uploads just those into their
Library the next time the Ken app is open. Then they're Library items like any other (`ken --library get <id>`).

    ken --roll days [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--kind video|photo]   what's on the phone, day by day
    ken --roll day YYYY-MM-DD [--kind video|photo]                                that day's items (ids to ask for)
    ken --roll send <id> [<id> …] [--wait SECONDS]                                the phone uploads those into the Library
    ken --roll wants                                                              what was asked for, and what's arrived
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


class Off(Exception):
    """No camera roll for this person (not hosted, not switched on, or they haven't let Ken see it yet)."""


def _site() -> tuple[str, str] | None:
    url, token = os.environ.get("KEN_KEEPALIVE_URL", ""), os.environ.get("KEN_KEEPALIVE_TOKEN", "")
    if not token or not url.startswith(("https://", "http://127.0.0.1", "http://localhost")) or "/box/" not in url:
        return None
    return url.rsplit("/box/", 1)[0] + "/box/roll", token


def call(path: str, body: dict | None = None, timeout: int = 30) -> dict:
    where = _site()
    if not where:
        raise Off()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{where[0]}/{path}", data=data, method="POST" if data is not None else "GET",
                                 headers={"Authorization": f"Bearer {where[1]}", "User-Agent": "Ken",
                                          **({"Content-Type": "application/json"} if data is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Off() from None
        raise RuntimeError(e.read().decode(errors="replace")[:300]) from None


def tz_minutes() -> int:
    """This computer's offset from UTC, so a 'day' is their day."""
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off else 0


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) - tz_minutes() * 60_000


def _opts(rest: list[str], names: tuple[str, ...]) -> tuple[dict, list[str]]:
    opts, words, i = {}, [], 0
    while i < len(rest):
        if rest[i] in names and i + 1 < len(rest):
            opts[rest[i]] = rest[i + 1]
            i += 2
        else:
            words.append(rest[i])
            i += 1
    return opts, words


def _clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%H:%M")


def main(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = args[0], args[1:]
    try:
        if cmd == "days":
            o, _ = _opts(rest, ("--from", "--to", "--kind"))
            q = {"tz": tz_minutes()}
            if "--from" in o:
                q["from"] = _ms(o["--from"])
            if "--to" in o:
                q["to"] = _ms(o["--to"])
            if o.get("--kind") in ("video", "photo"):
                q["kind"] = o["--kind"]
            r = call("days?" + urllib.parse.urlencode(q))
            days = r.get("days", [])
            if not r.get("total"):
                print("Their camera roll isn't here yet: they let Ken see it in the Ken app (Library tab), once.")
                return 0
            print(f"{len(days)} days (of {r['total']} photos and videos on their phone):" if days else "Nothing on those days.")
            for d in days:
                bits = [f"{d['videos']} video{'s' if d['videos'] != 1 else ''} ({int(d['seconds'] or 0)}s)" if d["videos"] else "",
                        f"{d['photos']} photo{'s' if d['photos'] != 1 else ''}" if d["photos"] else ""]
                print(f"{d['day']} ({datetime.strptime(d['day'], '%Y-%m-%d').strftime('%a')}) · " + ", ".join(b for b in bits if b))
            return 0
        if cmd == "day":
            o, words = _opts(rest, ("--kind",))
            if not words:
                print("Which day? ken --roll day YYYY-MM-DD", file=sys.stderr)
                return 2
            q = {"date": words[0], "tz": tz_minutes(), **({"kind": o["--kind"]} if o.get("--kind") in ("video", "photo") else {})}
            items = call("day?" + urllib.parse.urlencode(q)).get("items", [])
            print(f"{len(items)} on {words[0]}:" if items else f"Nothing on {words[0]}.")
            for it in items:
                shape = "vertical" if (it.get("h") or 0) > (it.get("w") or 0) else "horizontal" if it.get("w") else ""
                bits = [it["id"], _clock(it["t"]), it["k"], f"{it['d']:.0f}s" if it.get("d") else "", shape, it.get("s") or ""]
                print(" · ".join(b for b in bits if b))
            return 0
        if cmd == "send":
            o, ids = _opts(rest, ("--wait",))
            if not ids:
                print("Which ones? ken --roll send <id> …", file=sys.stderr)
                return 2
            call("want", {"ids": ids})
            wait = min(600, max(0, int(o.get("--wait", "90"))))
            deadline, arrived = time.time() + wait, {}
            while True:
                arrived = {w["id"]: w["library_id"] for w in call("wants").get("wants", []) if w["id"] in ids and w.get("sent_at")}
                if len(arrived) == len(ids) or time.time() >= deadline:
                    break
                time.sleep(5)
            for i in ids:
                print(f"{i}: in their Library as {arrived[i]}" if arrived.get(i) else f"{i}: waiting for their phone")
            if len(arrived) < len(ids):
                print("The phone sends them while the Ken app is open. Ask them to open it (or check again with ken --roll wants).")
            else:
                print("All here: ken --library get <library id> for the files.")
            return 0
        if cmd == "wants":
            for w in call("wants").get("wants", []):
                print(f"{w['id']}: " + (f"in their Library as {w['library_id']}" if w.get("sent_at") else "waiting for their phone"))
            return 0
    except Off:
        print("Their camera roll isn't switched on for them.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Camera roll: {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2
