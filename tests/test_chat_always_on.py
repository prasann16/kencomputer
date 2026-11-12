"""Chat always on: the real chat page, in a real (headless) Chrome, against a real Ken engine (a stand-in AI), must
show the conversation and get a reply whatever else goes wrong. Runs in CI before any change merges, and before every
website deploy (ken-hosted's build runs it on the screens it ships). Each case is something that broke chat for real:

  normal       the chat opens, a message sent from the box gets Ken's reply
  pictures     a chat full of pictures that load late still ends on the last message (2026-11-11)
  extras_fail  every request but the chat itself fails (settings, limits, lists, apps...): chat still shows and sends
  restarting   Ken answers nothing for 8 seconds (waking, updating): the chat appears once it's back, no error bar
  noisy_page   a browser extension throws "Script error." over and over: no error bar, no flood of reports (2026-11-12)
  quiet_page   an open, connected page stops re-asking every 15 seconds (98% of the site's requests, 2026-11-12), and
               a new message from Ken still shows at once over the live line

Skipped where there's no Chrome."""
import asyncio
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web as aioweb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

CHROME = next((p for p in (
    os.environ.get("KEN_CHECK_CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
) if p and Path(p).exists()), "")

pytestmark = pytest.mark.skipif(not CHROME, reason="no Chrome on this computer")

# The chat itself: everything else may fail and chat must still work.
CHAT = ("/api/chats/home", "/api/chats/home/messages", "/api/events", "/api/file")


def png(width: int, height: int, shade: int) -> bytes:
    raw = b"".join(b"\x00" + bytes([shade, 120, 200]) * width for _ in range(height))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Ken:
    """A Ken engine with a stand-in AI behind its real web server, misbehaving the way a case says."""

    def __init__(self, case: str, home: Path):
        from engine import Engine, HOME
        from test_engine import FakeBrain
        from web import WebApp
        self.case, self.reports, self.started, self.chat_loads = case, 0, time.monotonic(), 0
        home.mkdir(parents=True, exist_ok=True)
        e = Engine(home, FakeBrain())
        e.configure_home(home / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
        e.agents_dir.mkdir(); e.requests_dir.mkdir()
        pics = home / "work" / "pics"
        pics.mkdir(parents=True)
        for i in range(12):
            text = f"Message {i}: here's the plan for step {i}." if i % 2 else f"Can you do step {i}?"
            if case == "pictures" and i % 2:
                (pics / f"p{i}.png").write_bytes(png(300, 400, 20 * (i % 10)))
                text += f" Here it is: `{pics}/p{i}.png`"
            e.log_message(HOME, "ken" if i % 2 else "you", text)
        self.engine = e
        self.app = WebApp(e, home, rev="check")
        built = self.app.build()
        built.middlewares.insert(0, self.misbehave)
        self.built = built

    @aioweb.middleware
    async def misbehave(self, request, handler):
        path = request.path
        if path == "/api/ui-error":
            self.reports += 1
        if path == "/api/chats/home" and request.method == "GET":
            self.chat_loads += 1
        if self.case == "restarting" and path.startswith("/api/") and time.monotonic() - self.started < 8:
            return aioweb.Response(status=502, text="Ken is restarting")
        if self.case == "extras_fail" and path.startswith("/api/") and not path.startswith(CHAT) and path != "/api/ui-error":
            return aioweb.json_response({"error": "broken on purpose"}, status=500)
        if self.case == "pictures" and path == "/api/file":
            await asyncio.sleep(1.5)  # pictures arrive after the chat has been placed
        return await handler(request)


class Chrome:
    """Headless Chrome over its DevTools protocol: just what the cases need."""

    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="ken-check-chrome-")
        self.proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                      "--window-size=1200,800", f"--user-data-dir={self.profile}", "--remote-debugging-port=0", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.next_id, self.waiting, self.errors = 0, {}, []

    async def open(self, session: aiohttp.ClientSession):
        port_file = Path(self.profile) / "DevToolsActivePort"
        for _ in range(100):
            if port_file.exists() and port_file.read_text().strip():
                break
            await asyncio.sleep(0.1)
        port = port_file.read_text().split()[0]
        for _ in range(50):
            pages = [t for t in await (await session.get(f"http://127.0.0.1:{port}/json/list")).json() if t["type"] == "page"]
            if pages:
                break
            await asyncio.sleep(0.1)
        self.ws = await session.ws_connect(pages[0]["webSocketDebuggerUrl"], max_msg_size=0)
        self.reader = asyncio.ensure_future(self.read())
        await self.call("Runtime.enable")
        await self.call("Page.enable")

    async def read(self):
        async for msg in self.ws:
            data = json.loads(msg.data)
            if "id" in data and data["id"] in self.waiting:
                self.waiting.pop(data["id"]).set_result(data)
            elif data.get("method") == "Runtime.exceptionThrown":
                d = data["params"]["exceptionDetails"]
                self.errors.append((d.get("exception") or {}).get("description") or d.get("text"))

    async def call(self, method, **params):
        self.next_id += 1
        done = asyncio.get_running_loop().create_future()
        self.waiting[self.next_id] = done
        await self.ws.send_json({"id": self.next_id, "method": method, "params": params})
        return (await asyncio.wait_for(done, 20)).get("result", {})

    async def eval(self, expression):
        r = await self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        return (r.get("result") or {}).get("value")

    async def until(self, expression, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if await self.eval(expression):
                return True
            await asyncio.sleep(0.25)
        return False

    async def close(self):
        self.reader.cancel()
        await self.ws.close()
        self.proc.kill()  # this Chrome only, never the person's own
        self.proc.wait(timeout=10)
        shutil.rmtree(self.profile, ignore_errors=True)


MESSAGES = "document.querySelectorAll('.conversation-body .msg').length"
ERROR_BAR = "!!document.getElementById('page-error')"
GAP = "(() => { const b = document.querySelector('.conversation-body .scroll'); return b ? b.scrollHeight - b.scrollTop - b.clientHeight : 9999; })()"
SEND = """(() => {
  const box = document.querySelector('.chat-bottom textarea');
  if (!box) return false;
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(box, 'Are you there?');
  box.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()"""
CLICK_SEND = "(() => { const b = document.querySelector('.chat-bottom button[aria-label=\"Send\"]'); if (!b || b.disabled) return false; b.click(); return true; })()"
REPLIED = "[...document.querySelectorAll('.conversation-body .msg')].some((m) => m.textContent.includes('Are you there?')) && [...document.querySelectorAll('.conversation-body .msg')].some((m) => m.textContent.includes('did it'))"


async def open_chat(case: str, tmp_path: Path):
    ken = Ken(case, tmp_path / "home")
    runner = aioweb.AppRunner(ken.built)
    await runner.setup()
    site = aioweb.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    session = aiohttp.ClientSession()
    chrome = Chrome()
    await chrome.open(session)
    await chrome.call("Page.navigate", url=f"http://127.0.0.1:{port}/login?token={ken.app.token}")

    async def close():
        await chrome.close()
        await session.close()
        await runner.cleanup()
    return ken, chrome, close


async def sends_and_gets_a_reply(chrome: Chrome):
    assert await chrome.until(SEND, 5), "no message box"
    assert await chrome.until(CLICK_SEND, 5), "couldn't send"
    assert await chrome.until(REPLIED, 10), "sent, but no reply showed"


async def test_normal(tmp_path):
    ken, chrome, close = await open_chat("normal", tmp_path)
    try:
        assert await chrome.until(f"{MESSAGES} >= 12", 10), "the chat didn't show"
        await sends_and_gets_a_reply(chrome)
        assert not chrome.errors, chrome.errors
        assert not await chrome.eval(ERROR_BAR)
    finally:
        await close()


async def test_pictures_end_on_the_last_message(tmp_path):
    ken, chrome, close = await open_chat("pictures", tmp_path)
    try:
        assert await chrome.until(f"{MESSAGES} >= 12", 10), "the chat didn't show"
        assert await chrome.until("[...document.querySelectorAll('.msg-media img')].filter((i) => i.complete && i.naturalWidth).length >= 3", 15), \
            "the pictures didn't load"
        await asyncio.sleep(1)
        assert await chrome.eval(GAP) < 40, f"not on the last message once the pictures loaded ({await chrome.eval(GAP)}px above it)"
        assert not chrome.errors, chrome.errors
    finally:
        await close()


async def test_chat_works_when_everything_else_fails(tmp_path):
    ken, chrome, close = await open_chat("extras_fail", tmp_path)
    try:
        assert await chrome.until(f"{MESSAGES} >= 12", 10), "the chat didn't show while settings, limits and lists failed"
        await sends_and_gets_a_reply(chrome)
        assert not chrome.errors, chrome.errors
        assert not await chrome.eval(ERROR_BAR)
    finally:
        await close()


async def test_chat_comes_back_after_ken_restarts(tmp_path):
    ken, chrome, close = await open_chat("restarting", tmp_path)
    try:
        assert await chrome.until(f"{MESSAGES} >= 12", 30), "the chat never came back after Ken restarted"
        await sends_and_gets_a_reply(chrome)
        assert not chrome.errors, chrome.errors
        assert not await chrome.eval(ERROR_BAR)
    finally:
        await close()


async def test_a_noisy_browser_extension_changes_nothing(tmp_path):
    ken, chrome, close = await open_chat("noisy_page", tmp_path)
    try:
        assert await chrome.until(f"{MESSAGES} >= 12", 10), "the chat didn't show"
        await chrome.eval("for (let i = 0; i < 50; i++) window.dispatchEvent(new ErrorEvent('error', { message: 'Script error.' }))")
        await chrome.eval("for (let i = 0; i < 50; i++) window.dispatchEvent(new ErrorEvent('error', { message: 'boom', error: new Error('boom') }))")
        await asyncio.sleep(1)
        assert ken.reports == 1, f"{ken.reports} error reports sent (one real error, sent once, is right)"
        await sends_and_gets_a_reply(chrome)
    finally:
        await close()


async def test_an_open_page_listens_instead_of_asking(tmp_path):
    from engine import HOME
    ken, chrome, close = await open_chat("quiet_page", tmp_path)
    try:
        assert await chrome.until(f"{MESSAGES} >= 12", 10), "the chat didn't show"
        await asyncio.sleep(2)
        before = ken.chat_loads
        await asyncio.sleep(33)  # two 15-second rounds
        assert ken.chat_loads == before, f"asked for the chat {ken.chat_loads - before} more times while connected"
        ken.engine.log_message(HOME, "ken", "Pushed over the live line")
        assert await chrome.until("document.body.textContent.includes('Pushed over the live line')", 5), \
            "a new message from Ken didn't show without asking"
        assert not chrome.errors, chrome.errors
    finally:
        await close()
