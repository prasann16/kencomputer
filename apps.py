"""Hosted Ken: the person's apps (Gmail, Slack, Notion and 1,700+ more), connected on their host's Apps page.

The host knows which apps are connected and makes one-tap Connect links; this computer only asks it, with the
same token it checks in with (KEN_KEEPALIVE_TOKEN), so no app or provider key is ever here. Ken runs
`ken --apps` (what's connected) and `ken --apps notion` (is Notion connected, and if not, the link to connect it).

More than one account of an app (two Gmails): the apps tools pick one per call with `account` on each
COMPOSIO_MULTI_EXECUTE_TOOL item, by the id the apps tools themselves list (COMPOSIO_MANAGE_CONNECTIONS). The host's
list has other ids and no names, so the accounts Ken and Settings see come from the apps tools, each named once with
the app's own profile call (GMAIL_GET_PROFILE with that account: its address) and kept in app-accounts.json; the host
adds when each was connected. An account that can't be named is "Account 2 · connected Nov 5".
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT = 15
MCP_TIMEOUT = 30
# The call that says whose account it is, for the apps where we know it. Others are named by alias or number.
PROFILE = {
    "gmail": "GMAIL_GET_PROFILE",
    "github": "GITHUB_GET_THE_AUTHENTICATED_USER",
    "notion": "NOTION_GET_ABOUT_ME",
}
NAME_KEYS = ("emailAddress", "email", "user_email", "login", "username", "name")
RETRY_UNNAMED_S = 7 * 86400  # an account that couldn't be named is asked again after a week (each try is an app action)


def hosted() -> bool:
    return os.environ.get("KEN_KEEPALIVE_URL", "").startswith("https://") and bool(os.environ.get("KEN_KEEPALIVE_TOKEN"))


def _url(app: str = "") -> str:
    base = os.environ["KEN_KEEPALIVE_URL"].rsplit("/box/", 1)[0]
    return f"{base}/box/apps" + (f"?app={urllib.parse.quote(app)}" if app else "")


def fetch(app: str = "") -> dict:
    """{"connected": [{"slug", "name", "accounts": [...]}], "apps_url": ..., "match": {...} | None}."""
    # A named client: Cloudflare in front of the host refuses Python's default "Python-urllib" (403), as in voice.py.
    req = urllib.request.Request(_url(app), headers={"Authorization": f"Bearer {os.environ['KEN_KEEPALIVE_TOKEN']}", "User-Agent": "Ken"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


# ------------------------------------------------------------------ the apps tools (MCP), for accounts

class McpError(Exception):
    pass


def mcp_ready() -> bool:
    return os.environ.get("KEN_APPS_MCP_URL", "").startswith("https://") and bool(os.environ.get("KEN_APPS_MCP_TOKEN"))


def mcp_call(tool: str, arguments: dict):
    """One call to the apps MCP server (streamable HTTP, stateless): the tool's answer, parsed from JSON when it is."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}).encode()
    req = urllib.request.Request(os.environ["KEN_APPS_MCP_URL"], data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ['KEN_APPS_MCP_TOKEN']}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream", "User-Agent": "Ken"})
    with urllib.request.urlopen(req, timeout=MCP_TIMEOUT) as r:
        raw = r.read().decode("utf-8", "replace")
    # Either plain JSON or server-sent events, whose last `data:` line is the answer.
    events = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    reply = json.loads(events[-1] if events else raw)
    if "error" in reply:
        raise McpError(str(reply["error"].get("message") or reply["error"])[:300])
    result = reply.get("result") or {}
    text = "".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
    if result.get("isError"):
        raise McpError(text[:300] or "the apps tool failed")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _walk(obj):
    """Every dict inside a tool's answer (they nest it differently: data, results, response…)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def mcp_accounts(slugs: list[str]) -> dict[str, list[dict]]:
    """{slug: [{"id", "alias", "default"}]} for the active accounts the apps tools have, in the order they list them."""
    answer = mcp_call("COMPOSIO_MANAGE_CONNECTIONS", {"toolkits": [{"name": s, "action": "list"} for s in slugs]})
    out: dict[str, list[dict]] = {}
    for d in _walk(answer):
        for slug in slugs:
            listed = d.get(slug)
            if slug in out or not isinstance(listed, dict) or not isinstance(listed.get("accounts"), list):
                continue
            out[slug] = [{"id": str(a["id"]), "alias": str(a.get("alias") or "").strip()[:80] or None, "default": bool(a.get("is_default"))}
                         for a in listed["accounts"]
                         if isinstance(a, dict) and a.get("id") and str(a.get("status", "active")).lower() == "active"]
    return out


def profile_name(slug: str, account: str) -> str | None:
    """Whose account it is (an address, a login), from the app's own profile call with that account."""
    tool = PROFILE.get(slug)
    if not tool:
        return None
    answer = mcp_call("COMPOSIO_MULTI_EXECUTE_TOOL", {
        "tools": [{"tool_slug": tool, "arguments": {}, "account": account}],
        "thought": "Find out whose account this is, so Settings and Ken can tell their accounts apart."})
    for key in NAME_KEYS:  # the most telling kind of name first, wherever the answer puts it
        for d in _walk(answer):
            v = d.get(key)
            if isinstance(v, str) and v.strip() and len(v) <= 120:
                return v.strip()
    return None


def _cache_path() -> Path:
    return Path(os.environ.get("KEN_HOME", str(Path.home() / ".ken"))) / "app-accounts.json"


def _cache() -> dict:
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(path)
    except OSError:
        pass  # names are asked for again next time


def _when(ms) -> str:
    try:
        day = datetime.datetime.fromtimestamp(int(ms) / 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return f"{day:%b} {day.day}" + ("" if day.year == datetime.date.today().year else f", {day.year}")


def with_accounts(connected: list[dict], now: float | None = None) -> list[dict]:
    """The host's connected apps, each with its accounts named: [{"id", "name", "label", "default", "connected_at",
    "via"}], oldest first. `via` is "apps" (the apps tools' id: what Ken passes as `account`, and how it's
    disconnected) or "host" (the apps tools couldn't be asked: the host's id, disconnected on the host)."""
    now = time.time() if now is None else now
    hosted_lists = {a["slug"]: list(reversed(a.get("account_list") or [])) for a in connected}  # the host lists newest first
    listed: dict[str, list[dict]] = {}
    if mcp_ready() and connected:
        try:
            listed = mcp_accounts([a["slug"] for a in connected])
        except Exception:  # noqa: BLE001 - the host's list still says what's connected
            listed = {}
    cache, changed = _cache(), False
    out = []
    for app in connected:
        slug, from_host = app["slug"], hosted_lists.get(app["slug"], [])
        mine = list(reversed(listed.get(slug, [])))  # oldest first, as the host's
        accounts = []
        if mine:
            # The two lists can only be lined up by order, so a date (or the host's name) is borrowed only when they agree in size.
            agree = len(mine) == len(from_host)
            for n, a in enumerate(mine):
                key = f"{slug}:{a['id']}"
                hit = cache.get(key)
                if a["alias"] is None and (hit is None or (hit.get("name") is None and now - hit.get("at", 0) > RETRY_UNNAMED_S)):
                    try:
                        name = profile_name(slug, a["id"])
                    except Exception:  # noqa: BLE001 - unnamed for now
                        name = None
                    hit = cache[key] = {"name": name, "at": now}
                    changed = True
                host = from_host[n] if agree else {}
                accounts.append({"id": a["id"], "name": a["alias"] or (hit or {}).get("name") or host.get("label"),
                                 "default": a["default"], "connected_at": host.get("connected_at"), "via": "apps"})
        else:
            accounts = [{"id": h["id"], "name": h.get("label"), "default": False, "connected_at": h.get("connected_at"), "via": "host"}
                        for h in from_host]
        for n, a in enumerate(accounts, 1):
            when = _when(a["connected_at"]) if a["connected_at"] else ""
            a["label"] = a["name"] or f"Account {n}" + (f" · connected {when}" if when else "")
        out.append({"slug": slug, "name": app.get("name") or slug, "accounts": accounts})
    if changed:
        _save_cache(cache)
    return out


def disconnect(slug: str, account: str) -> None:
    """Remove one account through the apps tools (the same list it was named from), and forget its name."""
    mcp_call("COMPOSIO_MANAGE_CONNECTIONS", {"toolkits": [{"name": slug, "action": "remove", "account_id": account}]})
    if any(a["id"] == account for a in mcp_accounts([slug]).get(slug, [])):
        raise McpError("the account is still connected")
    cache = _cache()
    if cache.pop(f"{slug}:{account}", None) is not None:
        _save_cache(cache)


def _app_line(a: dict) -> str:
    accounts = a.get("accounts") or []
    head = f"{a['name']} ({a['slug']})"
    if not accounts:
        return head
    if isinstance(accounts[0], str):  # an older host: names only
        return f"{head}: {', '.join(accounts)}"
    if len(accounts) == 1:
        return f"{head}: {accounts[0]['label']}"
    def one(x):
        return f"{x['label']} [account: {x['id']}{', the default' if x.get('default') else ''}]"
    return f"{head}: {len(accounts)} accounts: " + "; ".join(one(x) for x in accounts)


def describe(data: dict, app: str = "") -> str:
    """What `ken --apps` prints, for Ken to read."""
    connected = data.get("connected") or []
    lines = []
    if connected:
        lines.append("Connected apps: " + "; ".join(_app_line(a) for a in connected))
        if any(len(a.get("accounts") or []) > 1 and isinstance(a["accounts"][0], dict) for a in connected):
            lines.append("For an app with more than one account, pass `account` (the id in brackets) on every apps call "
                         "for it: the one they mean (\"my work Gmail\"); if it isn't clear which, ask them once.")
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
        data = fetch(app)
        if data.get("connected"):
            data["connected"] = with_accounts(data["connected"])
        print(describe(data, app))
        return 0
    except Exception:  # noqa: BLE001 - say it plainly; Ken tells them
        print("Couldn't check their apps just now. Try once more in a minute; if it still fails, tell them plainly.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
