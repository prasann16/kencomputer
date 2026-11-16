"""Live view: the person does the one step only a person can do, in Ken's own browser.

On a computer in the cloud Ken's browser is headless, so when a page needs a person (a captcha picture grid, a
sign-in with a code sent to their phone, "confirm it's you") Ken calls `request_human(reason, url)` and sends them
the link it returns. The link opens a page that shows that browser tab live (Chrome's own screencast) and passes
their clicks, scrolling and typing back into it. They press Done, and Ken carries on in the same tab.

Why this way and not their own computer: nothing on their computer listens for connections, no cookies or
passwords move between machines, and the site sees one browser on one IP the whole time (a sign-in that starts on
one IP and finishes on another is what sites flag). The link is the only key:
  - signed with Ken's web token (HMAC-SHA256), which the hosting service also holds, so the host can check a link
    before it wakes or reaches this computer; nothing else can make one;
  - good for 15 minutes, ends at once on Done or Cancel, and one screen at a time watches it;
  - it shows one tab: no address bar, no other tabs, no DevTools.

Off unless KEN_LIVE_VIEW=1 (and Ken has its own browser). Chrome then also listens for DevTools on
127.0.0.1:KEN_LIVE_CDP_PORT (9335), inside this computer only; chrome-devtools-mcp keeps its own pipe.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import ClientSession, WSMsgType, web

log = logging.getLogger("ken")

TTL = 15 * 60
WAIT = 45  # one wait_for_human call, in seconds
VIEWER_JS = Path(__file__).parent / "web" / "live.js"
# Keys a person may press that aren't text: (key, code, Windows virtual key code, text it types).
KEYS = {
    "Enter": ("Enter", "Enter", 13, "\r"), "Backspace": ("Backspace", "Backspace", 8, ""), "Tab": ("Tab", "Tab", 9, ""),
    "Escape": ("Escape", "Escape", 27, ""), "Delete": ("Delete", "Delete", 46, ""), " ": (" ", "Space", 32, " "),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37, ""), "ArrowUp": ("ArrowUp", "ArrowUp", 38, ""),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39, ""), "ArrowDown": ("ArrowDown", "ArrowDown", 40, ""),
}


def enabled() -> bool:
    return os.environ.get("KEN_LIVE_VIEW") == "1"


def cdp_port() -> int:
    return int(os.environ.get("KEN_LIVE_CDP_PORT", "9335"))


def chrome_args() -> list[str]:
    """For chrome-devtools-mcp: Chrome also listens on a local DevTools port. Puppeteer keeps its pipe when one of
    the --remote-debugging- flags is already there only if the pipe is named too, so both are."""
    return ["--chromeArg=--remote-debugging-pipe", f"--chromeArg=--remote-debugging-port={cdp_port()}"]


def public_base() -> str:
    """Where the person opens Ken: the hosting service's site (KEN_PUBLIC_URL, else the site Ken checks in with),
    or this computer's own app server."""
    if base := os.environ.get("KEN_PUBLIC_URL", "").strip():
        return base.rstrip("/")
    if keepalive := os.environ.get("KEN_KEEPALIVE_URL", "").strip():
        u = urllib.parse.urlsplit(keepalive)
        return f"{u.scheme}://{u.netloc}"
    return f"http://127.0.0.1:{os.environ.get('KEN_WEB_PORT', '7777')}"


# ------------------------------------------------------------------ the link

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key: str, payload: dict) -> str:
    """`<payload>.<signature>`, both base64url: payload {"s": session, "e": expiry (epoch s), "u": whose Ken}."""
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return body + "." + _b64(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())


def verify(key: str, token: str, now: float | None = None) -> dict | None:
    """The payload of a link signed with `key` that hasn't expired, else None."""
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(_unb64(sig), hmac.new(key.encode(), body.encode(), hashlib.sha256).digest()):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("e"), (int, float)):
        return None
    return payload if payload["e"] > (time.time() if now is None else now) else None


# ------------------------------------------------------------------ input

def key_events(key: str) -> list[dict]:
    """CDP Input.dispatchKeyEvent params for one press of a key that isn't plain text."""
    k, code, vk, text = KEYS[key]
    down = {"type": "keyDown", "key": k, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
    if text:
        down |= {"text": text, "unmodifiedText": text}
    return [down, {"type": "keyUp", "key": k, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}]


def text_events(text: str) -> list[tuple[str, dict]]:
    """Typing: one key press per character (sites that watch keystrokes, like code boxes, see real keys); long
    text (a pasted password manager entry) goes in at once."""
    if len(text) > 200:
        return [("Input.insertText", {"text": text})]
    out = []
    for ch in text:
        if ch == "\n":
            out += [("Input.dispatchKeyEvent", e) for e in key_events("Enter")]
            continue
        out += [("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "text": ch, "unmodifiedText": ch}),
                ("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})]
    return out


def to_page(meta: dict, fx: float, fy: float) -> tuple[float, float]:
    """A point on the viewer's picture (0..1 across and down) in the page's own CSS pixels."""
    fx, fy = min(max(float(fx), 0.0), 1.0), min(max(float(fy), 0.0), 1.0)
    scale = meta.get("pageScaleFactor") or 1
    return fx * meta.get("deviceWidth", 0) / scale, (fy * meta.get("deviceHeight", 0) - meta.get("offsetTop", 0)) / scale


def tab_size(cw: int, ch: int) -> tuple[int, int]:
    """The tab's size while a person watches (CSS pixels): the viewer's width, and tall enough for a whole picture
    challenge with its Verify button, so nobody has to find it by scrolling (the picture is shrunk to fit their
    screen instead). A phone gets its own narrow layout, a little less tall."""
    w = min(max(cw, 360), 1440)
    return w, min(max(ch, 800 if w >= 600 else 680), 1200)


def pick_target(pages: list[dict], url: str) -> dict | None:
    """The browser tab the person should see: the one at `url` (exactly, then same address without the query, then
    same site), else the only tab there is."""
    pages = [p for p in pages if p.get("type") == "page" and p.get("webSocketDebuggerUrl")]
    if url:
        want = urllib.parse.urlsplit(url)
        for same in (lambda u: u.geturl() == want.geturl(),
                     lambda u: (u.netloc, u.path) == (want.netloc, want.path),
                     lambda u: u.netloc == want.netloc):
            found = [p for p in pages if same(urllib.parse.urlsplit(p.get("url", "")))]
            if found:
                return found[0]
    return pages[0] if len(pages) == 1 else None


# ------------------------------------------------------------------ sessions

@dataclass
class Session:
    id: str
    target: dict
    reason: str
    url: str
    expires: float
    outcome: str = ""  # done | cancelled | expired | ended
    watching: bool = False
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    viewer: web.WebSocketResponse | None = None
    waiters: int = 0


class LiveView:
    def __init__(self, key: str, cdp: str | None = None, base: str | None = None, owner: str | None = None,
                 on_end: Callable[[Session], Awaitable[None]] | None = None,
                 on_start: Callable[[Session, str], None] | None = None):
        self.key = key
        self.cdp = cdp or f"http://127.0.0.1:{cdp_port()}"
        self.base = base or public_base()
        self.owner = owner if owner is not None else os.environ.get("KEN_ANALYTICS_ID", "")
        self.on_end = on_end
        self.on_start = on_start  # sends the person the link (bot.py): not left to the model to remember
        self.sessions: dict[str, Session] = {}
        self.tasks: set[asyncio.Task] = set()

    async def _pages(self) -> list[dict]:
        async with ClientSession() as s, s.get(f"{self.cdp}/json/list", timeout=5) as r:
            return await r.json(content_type=None)

    async def start(self, reason: str, url: str = "") -> dict:
        """A link to the tab at `url` (opened if Ken hasn't got it open). Raises RuntimeError in plain words."""
        try:
            pages = await self._pages()
        except Exception as exc:
            raise RuntimeError("Ken's browser isn't reachable for a live view (is KEN_LIVE_VIEW on and the browser open?)") from exc
        target = pick_target(pages, url)
        if target is None and url.startswith(("http://", "https://")):
            async with ClientSession() as s, s.put(f"{self.cdp}/json/new?{urllib.parse.quote(url, safe='')}", timeout=10) as r:
                target = await r.json(content_type=None)
        if target is None:
            raise RuntimeError("Which page? Several tabs are open: pass the page's url.")
        for old in [s for s in self.sessions.values() if s.target.get("id") == target["id"] and not s.outcome]:
            await self.end(old.id, "ended")  # a fresh link for the same tab replaces the old one
        sid = secrets.token_urlsafe(9)
        expires = time.time() + TTL
        payload = {"s": sid, "e": int(expires)} | ({"u": self.owner} if self.owner else {})
        session = Session(sid, target, reason[:300], target.get("url", url), expires)
        self.sessions[sid] = session
        self._spawn(self._expire(session))
        link = f"{self.base}/live/{sign(self.key, payload)}"
        sent = False
        if self.on_start:
            try:
                self.on_start(session, link)
                sent = True
            except Exception as exc:
                log.warning("live view: couldn't send the link: %s", exc)
        return {"id": sid, "link": link, "expires_in_minutes": TTL // 60, "page": session.url, "sent": sent}

    async def _expire(self, session: Session) -> None:
        try:
            await asyncio.wait_for(session.ended.wait(), max(session.expires - time.time(), 0))
        except asyncio.TimeoutError:
            await self.end(session.id, "expired")

    async def end(self, sid: str, outcome: str) -> None:
        session = self.sessions.get(sid)
        if not session or session.outcome:
            return
        session.outcome = outcome
        if self.on_end:
            self._spawn(self.on_end(session))
        # Kept a while for wait_for_human and the page's "this link has ended", then forgotten.
        self._spawn(self._forget(sid))
        try:
            if session.viewer is not None and not session.viewer.closed:
                await session.viewer.send_json({"type": "ended", "outcome": outcome})
                await session.viewer.close()
        except Exception:  # the page already went away
            pass
        finally:
            # Last: it stops the relay, and Done arrives on the relay's own task.
            session.ended.set()

    async def _forget(self, sid: str) -> None:
        await asyncio.sleep(TTL)
        self.sessions.pop(sid, None)

    async def wait(self, sid: str, seconds: float) -> dict:
        session = self.sessions.get(sid)
        if not session:
            return {"status": "unknown", "say": "No such live view (it may be over an hour old)."}
        session.waiters += 1
        try:
            await asyncio.wait_for(session.ended.wait(), min(max(seconds, 1), 600))
        except asyncio.TimeoutError:
            pass
        finally:
            session.waiters -= 1
        return self.status(session)

    def status(self, session: Session) -> dict:
        state = session.outcome or ("watching" if session.watching else "not opened yet")
        return {"status": state, "page": session.url, "minutes_left": max(0, round((session.expires - time.time()) / 60))}

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def _session(self, request: web.Request) -> tuple[Session | None, str]:
        payload = verify(self.key, request.match_info.get("token", ""))
        if not payload:
            return None, "This link has expired. Ask Ken for a new one."
        session = self.sessions.get(str(payload.get("s", "")))
        if not session:
            return None, "This link has ended. Ask Ken for a new one if you still need it."
        if session.outcome:
            return None, {"done": "Done. Ken has carried on.", "cancelled": "Cancelled. Ken knows.",
                          "expired": "This link has expired. Ask Ken for a new one."}.get(session.outcome, "This link has ended.")
        return session, ""

    # ------------------------------------------------------------ the page

    async def page(self, request: web.Request) -> web.Response:
        session, problem = self._session(request)
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                   "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                                              "connect-src 'self' ws: wss:; img-src data:; base-uri 'none'; frame-ancestors 'none'"}
        title = "Ken needs you for a moment"
        if not session:
            return web.Response(text=_PAGE.format(title="Ken", reason=_esc(problem), site="", script=""),
                                content_type="text/html", headers=headers, status=410)
        site = urllib.parse.urlsplit(session.url).netloc
        token = request.match_info["token"]
        return web.Response(text=_PAGE.format(title=title, reason=_esc(session.reason), site=_esc(site),
                                              script=f'<script src="/live/{token}/viewer.js"></script>'),
                            content_type="text/html", headers=headers)

    async def script(self, request: web.Request) -> web.Response:
        session, _ = self._session(request)
        if not session:
            return web.Response(status=410)
        return web.Response(text=VIEWER_JS.read_text(), content_type="application/javascript",
                            headers={"Cache-Control": "no-store"})

    async def socket(self, request: web.Request) -> web.StreamResponse:
        session, problem = self._session(request)
        if not session:
            return web.Response(text=problem, status=410)
        if session.viewer is not None and not session.viewer.closed:
            return web.Response(text="This link is already open on another screen.", status=409)
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        session.viewer, session.watching = ws, True
        try:
            await self._relay(session, ws)
        except Exception as exc:  # the browser tab closed, or Chrome went away
            log.warning("live view %s: %s", session.id, exc)
            if not ws.closed:
                await ws.send_json({"type": "error", "text": "Lost Ken's browser tab. Ask Ken for a new link."})
        finally:
            session.watching = False
            if not ws.closed:
                await ws.close()
        return ws

    async def _relay(self, session: Session, ws: web.WebSocketResponse) -> None:
        """Chrome's screencast of the tab to the person; their mouse and keys to the tab."""
        meta: dict = {}
        fitted: list = []
        ids = iter(range(1, 1 << 30))
        async with ClientSession() as http, http.ws_connect(session.target["webSocketDebuggerUrl"], max_msg_size=0) as cdp:
            async def send(method: str, params: dict | None = None) -> None:
                await cdp.send_json({"id": next(ids), "method": method, "params": params or {}})

            async def from_chrome() -> None:
                async for msg in cdp:
                    if msg.type != WSMsgType.TEXT:
                        break
                    data = json.loads(msg.data)
                    if data.get("method") == "Page.screencastFrame":
                        p = data["params"]
                        meta.clear()
                        meta.update(p["metadata"])
                        await send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
                        if not ws.closed:
                            await ws.send_json({"type": "frame", "w": meta.get("deviceWidth"), "h": meta.get("deviceHeight")})
                            await ws.send_bytes(base64.b64decode(p["data"]))
                    elif data.get("method") == "Inspector.detached" or data.get("method") == "Target.detachedFromTarget":
                        break

            async def from_person() -> None:
                async for msg in ws:
                    if msg.type != WSMsgType.TEXT:
                        break
                    try:
                        m = json.loads(msg.data)
                    except ValueError:
                        continue
                    kind = m.get("type")
                    if kind == "hello":
                        w, h = int(m.get("w") or 1280), int(m.get("h") or 800)
                        await send("Page.bringToFront")
                        # The tab takes the viewer's width (a phone sees the site's own narrow layout at a readable
                        # size) and a tall height (tab_size); put back when the view ends. Same browser, so the site
                        # sees nothing new.
                        cw, ch = int(m.get("cw") or 0), int(m.get("ch") or 0)
                        if cw and ch:
                            fitted.append(1)
                            width, height = tab_size(cw, ch)
                            await send("Emulation.setDeviceMetricsOverride", {
                                "width": width, "height": height, "deviceScaleFactor": 0, "mobile": False})
                        await send("Page.startScreencast", {"format": "jpeg", "quality": 70,
                                                            "maxWidth": min(max(w, 320), 1920), "maxHeight": min(max(h, 320), 1920)})
                    elif kind == "mouse" and meta:
                        x, y = to_page(meta, m.get("x", 0), m.get("y", 0))
                        action = {"down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved"}.get(m.get("action"))
                        if action:
                            await send("Input.dispatchMouseEvent", {"type": action, "x": x, "y": y, "button": "left" if action != "mouseMoved" else "none",
                                                                    "buttons": 1 if action == "mousePressed" else 0,
                                                                    "clickCount": 1 if action != "mouseMoved" else 0})
                    elif kind == "wheel" and meta:
                        x, y = to_page(meta, m.get("x", 0.5), m.get("y", 0.5))
                        await send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y,
                                                                "deltaX": float(m.get("dx", 0)), "deltaY": float(m.get("dy", 0))})
                    elif kind == "text" and isinstance(m.get("text"), str):
                        for method, params in text_events(m["text"][:5000]):
                            await send(method, params)
                    elif kind == "key" and m.get("key") in KEYS:
                        for params in key_events(m["key"]):
                            await send("Input.dispatchKeyEvent", params)
                    elif kind in ("done", "cancel"):
                        await send("Page.stopScreencast")
                        await self.end(session.id, "done" if kind == "done" else "cancelled")
                        break

            await ws.send_json({"type": "ready", "minutes_left": max(0, int((session.expires - time.time()) // 60))})
            chrome, person = asyncio.create_task(from_chrome()), asyncio.create_task(from_person())
            await asyncio.wait({chrome, person, asyncio.create_task(session.ended.wait())}, return_when=asyncio.FIRST_COMPLETED)
            for t in (chrome, person):
                t.cancel()
            if not cdp.closed:
                await cdp.send_json({"id": next(ids), "method": "Page.stopScreencast", "params": {}})
                if fitted:
                    await cdp.send_json({"id": next(ids), "method": "Emulation.clearDeviceMetricsOverride", "params": {}})

    # ------------------------------------------------------------ the agent's tools

    TOOLS = [
        {"name": "request_human", "description": (
            "When a page in your browser needs the person (a captcha you can't pass, a sign-in, a code sent to their "
            "phone, 'confirm it's you'): returns a link that shows them that tab live, where they do the step "
            "themselves; the link is sent to their chat for you. Then call wait_for_human. The link works for 15 minutes and ends when they press Done. Never use "
            "it to get their password typed for you to read."),
         "inputSchema": {"type": "object", "properties": {
             "reason": {"type": "string", "description": (
                 "What they should do, in a few plain words, shown above the page. Name the kind of step ('solve the "
                 "picture check', 'sign in to Google'), not what's in it now ('select the buses'): a challenge "
                 "changes while they look.")},
             "url": {"type": "string", "description": "The page that needs them (the tab already open there is used)."}},
             "required": ["reason"]}},
        {"name": "wait_for_human", "description": (
            "Wait up to 45 seconds for the person to finish a live view from request_human. Returns 'done' (carry on "
            "in the same tab: take a snapshot first), 'cancelled', 'expired', or 'watching' / 'not opened yet' with "
            "minutes_left: then call it again (quietly) until it ends; don't guess how long has passed."),
         "inputSchema": {"type": "object", "properties": {
             "id": {"type": "string"}, "seconds": {"type": "integer", "description": "Up to 45 (default 45)."}},
             "required": ["id"]}},
    ]

    async def call(self, name: str, args: dict) -> dict:
        """An MCP tools/call result for one of TOOLS."""
        try:
            if name == "request_human":
                out = await self.start(str(args.get("reason") or "Ken needs you for a moment"), str(args.get("url") or ""))
                out["say"] = (f"The link is already in their chat (don't repeat it). Call wait_for_human with id {out['id']}."
                              if out.pop("sent") else
                              f"Now write them a message with this link and one line on what to do: {out['link']} "
                              f"(works for {out['expires_in_minutes']} minutes). Then call wait_for_human with id {out['id']}.")
            else:
                # Under the MCP client's own 60 second limit on a tool call: a longer wait is several calls.
                out = await self.wait(str(args.get("id", "")), min(float(args.get("seconds") or WAIT), WAIT))
                if out["status"] in ("watching", "not opened yet"):
                    out["say"] = "Not finished yet. Call wait_for_human again."
            return {"content": [{"type": "text", "text": json.dumps(out)}]}
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"Failed: {exc}"}], "isError": True}

    def routes(self, router: web.UrlDispatcher) -> None:
        router.add_get("/live/{token}", self.page)
        router.add_get("/live/{token}/viewer.js", self.script)
        router.add_get("/live/{token}/ws", self.socket)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex"><title>{title}</title>
<style>
*{{box-sizing:border-box}} html,body{{margin:0;height:100%;background:#F6F4EF;color:#1B1A17;font:15px/1.4 system-ui,-apple-system,sans-serif}}
header{{padding:12px 16px;display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}}
header p{{margin:0}} .site{{color:#6B675F;font-size:13px}}
#screen{{display:block;margin:0 auto;max-width:100%;max-height:calc(100vh - 170px);background:#fff;box-shadow:0 1px 6px rgba(0,0,0,.15);touch-action:none;cursor:default;outline:none}}
#screen:focus{{box-shadow:0 0 0 2px #1B1A17}}
.bar{{display:flex;gap:8px;padding:10px 16px;flex-wrap:wrap;align-items:center;justify-content:center}}
button{{font:inherit;padding:9px 14px;border-radius:10px;border:1px solid #1B1A17;background:#fff;color:#1B1A17}}
button.main{{background:#1B1A17;color:#fff}} input{{font:16px system-ui;padding:9px 10px;border-radius:10px;border:1px solid #C9C4B8;min-width:0;flex:1 1 160px}}
#status{{text-align:center;color:#6B675F;font-size:13px;padding:4px 16px 16px}}
</style></head><body>
<header><div><p><b>{reason}</b></p><p class="site">{site} &middot; Ken's browser, live. Tap or click in it like a normal page.</p></div>
<div><button id="cancel">Can't do it</button> <button id="done" class="main">Done</button></div></header>
<canvas id="screen" tabindex="0" width="10" height="10"></canvas>
<div class="bar"><input id="text" placeholder="Type here, then Send" autocomplete="off" autocapitalize="off" spellcheck="false">
<button id="send">Send</button><button data-key="Backspace">&#9003;</button><button data-key="Tab">Tab</button><button data-key="Enter">Enter</button></div>
<div id="status">Connecting&hellip;</div>
{script}
</body></html>"""
