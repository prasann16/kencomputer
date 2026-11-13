"""Live view (liveview.py): signed links, the person's input, and how it plugs into the shared browser server."""
import asyncio
import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import liveview
from ken_mcp import SharedServer

KEY = "k" * 32


def test_link_is_signed_and_expires():
    tok = liveview.sign(KEY, {"s": "abc", "e": int(time.time()) + 60, "u": "user-1"})
    assert liveview.verify(KEY, tok)["s"] == "abc"
    assert liveview.verify("other-key" * 4, tok) is None  # another Ken's key
    body, sig = tok.split(".")
    forged = liveview.sign("x" * 32, {"s": "abc", "e": int(time.time()) + 9999})
    assert liveview.verify(KEY, forged.split(".")[0] + "." + sig) is None  # longer life, old signature
    assert liveview.verify(KEY, tok, now=time.time() + 61) is None  # expired
    assert liveview.verify(KEY, "garbage") is None and liveview.verify(KEY, "a.b.c") is None


def test_point_maps_to_page_pixels():
    meta = {"deviceWidth": 400, "deviceHeight": 800, "pageScaleFactor": 1, "offsetTop": 0}
    assert liveview.to_page(meta, 0.5, 0.25) == (200, 200)
    assert liveview.to_page(meta, 2, -1) == (400, 0)  # clamped to the picture


def test_typing_is_key_presses_and_long_text_is_inserted():
    ev = liveview.text_events("a\n")
    assert [p["type"] for _, p in ev] == ["keyDown", "keyUp", "keyDown", "keyUp"] and ev[2][1]["key"] == "Enter"
    assert liveview.text_events("x" * 201) == [("Input.insertText", {"text": "x" * 201})]


def test_picks_the_tab_at_the_url():
    pages = [{"type": "page", "url": "https://a.com/x?q=1", "webSocketDebuggerUrl": "ws://1", "id": "1"},
             {"type": "page", "url": "https://b.com/login", "webSocketDebuggerUrl": "ws://2", "id": "2"}]
    assert liveview.pick_target(pages, "https://b.com/login")["id"] == "2"
    assert liveview.pick_target(pages, "https://a.com/x")["id"] == "1"
    assert liveview.pick_target(pages, "") is None  # several tabs and no url: ask which
    assert liveview.pick_target(pages[:1], "")["id"] == "1"


async def test_link_ends_on_done_and_says_so():
    live = liveview.LiveView(KEY, cdp="http://127.0.0.1:1", base="https://ken.example", owner="u1")
    target = {"id": "T", "url": "https://site.test/", "webSocketDebuggerUrl": "ws://x"}

    async def pages():
        return [{**target, "type": "page"}]
    live._pages = pages
    out = await live.start("Tick the box", "https://site.test/")
    assert out["link"].startswith("https://ken.example/live/") and out["expires_in_minutes"] == 15
    app = web.Application()
    live.routes(app.router)
    async with TestClient(TestServer(app)) as client:
        path = "/live/" + out["link"].rsplit("/", 1)[1]
        page = await client.get(path)
        assert page.status == 200 and "Tick the box" in await page.text()
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        assert (await client.get(path[:-2] + "xx")).status == 410  # tampered
        waiting = asyncio.create_task(live.wait(out["id"], 5))
        await live.end(out["id"], "done")
        assert (await waiting)["status"] == "done"
        assert (await client.get(path)).status == 410  # over: the link is dead


async def test_shared_browser_server_lists_and_answers_live_tools():
    live = liveview.LiveView(KEY, cdp="http://127.0.0.1:1", base="https://ken.example")
    server = SharedServer("true", [], extra=live)

    async def ready():
        return {}
    server.ready = ready
    sent = []

    def fake_send(method, params):
        sent.append(method)
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({"result": {"tools": [{"name": "click"}]}})
        return fut
    server._send = fake_send
    listed = await server.handle({"id": 1, "method": "tools/list", "params": {}})
    assert [t["name"] for t in listed["result"]["tools"]] == ["click", "request_human", "wait_for_human"]
    called = await server.handle({"id": 2, "method": "tools/call", "params": {"name": "wait_for_human", "arguments": {"id": "nope"}}})
    assert "unknown" in called["result"]["content"][0]["text"] and sent == ["tools/list"]  # answered here, not by Chrome


async def test_one_wait_stays_under_the_mcp_clients_minute(monkeypatch):
    live = liveview.LiveView(KEY, cdp="http://127.0.0.1:1", base="https://ken.example")
    asked = []

    async def wait(sid, seconds):
        asked.append(seconds)
        return {"status": "not opened yet", "page": "", "minutes_left": 14}
    live.wait = wait
    out = await live.call("wait_for_human", {"id": "x", "seconds": 600})
    assert asked == [liveview.WAIT] and liveview.WAIT < 60 and "again" in out["content"][0]["text"]
