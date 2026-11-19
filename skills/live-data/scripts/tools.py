"""Live data tools on hosted Ken: treg's catalog (viral reels, public profiles and posts, trends, SEO, web search…)
through the host's /box/tools, on Ken's own treg key, each call paid from their Ken AI credits (the host adds its
margin). Run as `<ken> --tools <command>`; every command prints JSON.

    status                                  switched on? Ken AI credits left
    search "<what you need>"                tools that do it, with prices (free)
    tool <id>                               one tool: parameters, price, an example (free)
    call <id> --max 0.05 [--data '{…}' | --data @body.json] [--query k=v …] [--label "what for"] [--out file.json]

A call never costs more than --max (dollars of treg's price; their credits pay that plus the margin), and never more
than $1. Small answers print as they came; a big one is saved to a file and its path printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PRINT_MOST = 40_000  # characters of an answer printed here; a bigger one goes to a file
NOT_HOSTED = "Live data tools come with Ken's hosted plans (kencomputer.dev); this computer isn't on one, so nothing ran."


def host() -> tuple[str, str] | None:
    """Hosted Ken's /box/tools and this computer's Ken AI credits token, or None."""
    url = os.environ.get("KEN_TOOLS_URL", "").strip().rstrip("/")
    if not url:
        ai = os.environ.get("KEN_INCLUDED_AI_URL", "").strip().rstrip("/")
        if ai.startswith("https://") and ai.endswith("/ai"):
            url = ai[: -len("/ai")] + "/box/tools"
    token = os.environ.get("KEN_INCLUDED_AI_TOKEN", "").strip()
    local = url.startswith(("http://localhost", "http://127.0.0.1"))  # a test copy of the host
    return (url, token) if token and (url.startswith("https://") or local) else None


def out(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def request(via: tuple[str, str], method: str, path: str, body: dict | None = None, label: str = "",
            timeout: int = 180) -> tuple[int, dict, str]:
    # A named client: Cloudflare in front of the host refuses Python's default "Python-urllib".
    headers = {"Authorization": f"Bearer {via[1]}", "User-Agent": "Ken"}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if label:
        headers["X-Ken-Label"] = urllib.parse.quote(label[:80])
    req = urllib.request.Request(via[0] + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode("utf-8", "replace")


def fail(status: int, text: str) -> int:
    try:
        d = json.loads(text)
        out({"error": d.get("error") or text[:600], "code": d.get("code"), **{k: v for k, v in d.items() if k.endswith("_usd")}})
    except ValueError:
        if status in (404, 405) or "<html" in text[:200].lower():
            out({"error": "The live data tools aren't live on this computer's host yet, so nothing ran.", "code": "not_live"})
        else:
            out({"error": text[:600] or f"HTTP {status}", "code": None})
    return 1


def parse_data(raw: str | None):
    if raw is None:
        return None
    text = Path(raw[1:]).read_text() if raw.startswith("@") else raw
    return json.loads(text)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="ken --tools", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("search")
    s.add_argument("what", nargs="+")
    t = sub.add_parser("tool")
    t.add_argument("id")
    c = sub.add_parser("call")
    c.add_argument("id")
    c.add_argument("--max", type=float, required=True, help="the most this call may cost, in dollars (treg's price)")
    c.add_argument("--data", help="JSON body, or @file.json")
    c.add_argument("--query", action="append", default=[], help="key=value, for a GET tool (repeatable)")
    c.add_argument("--label", default="", help="what it's for, as their credits list shows it")
    c.add_argument("--out", help="save the answer here")
    c.add_argument("--key", help="an idempotency key: repeat a timed-out call with the same key and it isn't paid twice")
    a = p.parse_args(argv)

    via = host()
    if not via:
        out({"error": NOT_HOSTED, "code": "not_hosted"})
        return 1

    if a.cmd == "status":
        status, _, text = request(via, "GET", "/status", timeout=30)
    elif a.cmd == "search":
        status, _, text = request(via, "GET", "/search?" + urllib.parse.urlencode({"q": " ".join(a.what)}), timeout=60)
    elif a.cmd == "tool":
        status, _, text = request(via, "GET", f"/tool/{urllib.parse.quote(a.id, safe='')}", timeout=60)
    else:
        query = dict(q.split("=", 1) for q in a.query if "=" in q)
        try:
            body = parse_data(a.data)
        except (OSError, ValueError) as e:
            out({"error": f"--data isn't JSON or a readable @file: {e}", "code": "bad_data"})
            return 1
        ask = {"max_usd": a.max, "query": query or None, "body": body, "idempotency_key": a.key}
        status, headers, text = request(via, "POST", f"/call/{urllib.parse.quote(a.id, safe='')}",
                                        {k: v for k, v in ask.items() if v is not None}, label=a.label)
        if status >= 400:
            return fail(status, text)
        low = {k.lower(): v for k, v in headers.items()}
        info = {"charged_usd": float(low.get("x-ken-charged") or 0), "credits_left_usd": low.get("x-ken-credits-left"),
                "call_id": low.get("x-ken-call-id")}
        if a.out or len(text) > PRINT_MOST:
            path = Path(a.out or f"live-data/{a.id}-{time.strftime('%Y%m%d-%H%M%S')}.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            out({**info, "saved": str(path.resolve()), "chars": len(text)})
        else:
            out(info)
            print(text)
        return 0

    if status >= 400:
        return fail(status, text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
