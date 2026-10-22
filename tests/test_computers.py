"""Two real HTTP engines, with model execution controlled by the test."""
import asyncio
import json
import os
import stat
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web, WSMsgType
from yarl import URL

from computers import Computers, server_url
from engine import Engine
from web import WebApp
from test_engine import FakeBrain, FakeSession


def make_engine(home, brain=None):
    home.mkdir(exist_ok=True)
    e = Engine(home, brain or FakeBrain())
    e.configure_home(home / "work", lambda: "KEN", lambda: "KEN")
    e.agents_dir.mkdir(exist_ok=True)
    e.requests_dir.mkdir(exist_ok=True)
    return e


async def client_for(e, aiohttp_client):
    app = WebApp(e, e.home, rev="test")
    client = await aiohttp_client(app.build())
    client.session.headers["Authorization"] = "Bearer " + app.token
    return app, client


@pytest.fixture
async def pair(tmp_path, aiohttp_client):
    se, ce = make_engine(tmp_path / "server"), make_engine(tmp_path / "client")
    sa, server = await client_for(se, aiohttp_client)
    ca, client = await client_for(ce, aiohttp_client)
    settings = await (await server.put("/api/computers/server", json={"enabled": True})).json()
    added = await client.post("/api/computers", json={"name": "Home Mac", "url": str(server.make_url("")), "key": settings["key"]})
    assert added.status == 200, await added.text()
    computer = await added.json()
    yield {"se": se, "ce": ce, "sa": sa, "ca": ca, "server": server, "client": client,
           "computer": computer, "prefix": "/remote/" + computer["id"], "key": settings["key"]}
    for app in (sa, ca):
        for t in app.bg:
            t.cancel()
        await asyncio.gather(*app.bg, return_exceptions=True)


def test_addresses_and_credential_persistence(tmp_path):
    assert server_url(" https://ken.example.com/ ") == "https://ken.example.com"
    assert server_url("http://127.0.0.1:7777") == "http://127.0.0.1:7777"
    for bad in ("http://example.com", "https://user:secret@example.com", "https://example.com/api", "https://example.com?token=a", "https://example.com#foo", "file:///tmp/x", "https://example.com:bad"):
        with pytest.raises(ValueError):
            server_url(bad)
    c = Computers(tmp_path)
    assert not c.data["server"]["enabled"]
    c.data["server"].update(enabled=True, key="secret")
    c.save()
    assert Computers(tmp_path).data["server"]["key"] == "secret"
    c.data["server"]["key"] = "replacement"
    c.save()
    assert Computers(tmp_path).data["server"]["key"] == "replacement"


@pytest.mark.skipif(os.name != "posix", reason="Windows permissions use ACLs, not POSIX mode bits")
def test_credentials_stay_private_on_create_and_replace(tmp_path):
    c = Computers(tmp_path)
    c.save()
    assert stat.S_IMODE(c.path.stat().st_mode) == 0o600
    c.path.chmod(0o644)
    c.save()
    assert stat.S_IMODE(c.path.stat().st_mode) == 0o600


async def test_optional_server_and_local_only_controls(pair):
    p = pair
    state = await (await p["client"].get("/api/computers/server")).json()
    assert state["enabled"] is False and "key" not in state
    remote = {"Authorization": "Bearer " + p["key"], "X-Ken-Remote": "1"}
    for path in ("/api/computers", "/api/computers/server", "/api/computers/cloudflare", "/remote/1234567812345678/api/meta", "/", "/app.js"):
        assert (await p["server"].get(path, headers=remote)).status == 403
    assert (await p["server"].get("/api/identity", headers=remote)).status == 200
    # The tunnel reaches loopback, but the local app token must not authenticate there.
    for headers in ({"CF-Connecting-IP": "203.0.113.1"}, {"X-Forwarded-For": "203.0.113.1"}, {"Host": "ken.example.com"}):
        assert (await p["server"].get("/api/computers/server", headers=headers)).status == 401
    assert (await p["server"].put("/api/computers/server", json={"enabled": False}, headers={"Origin": "https://evil.example"})).status == 403
    assert (await p["server"].get("/api/meta", headers={"Authorization": "Bearer café", "X-Ken-Remote": "1"})).status == 401
    assert (await p["server"].post("/api/computers/cloudflare/install", headers=remote)).status == 403
    assert (await p["client"].post("/api/computers/cloudflare/install", json={})).status == 400
    assert p["ca"].computers.cloudflare.install_task is None


async def test_tunnel_verification_checks_this_server_and_websockets(pair):
    p = pair
    address = str(p["server"].make_url(""))
    r = await p["server"].post("/api/computers/cloudflare/verify", json={"url": address})
    assert r.status == 200, await r.text()
    result = await r.json()
    assert result["url"] == address.rstrip("/") and result["local_only"]
    # Pasting the service address must not be mistaken for a public tunnel.
    assert "url" not in Computers(p["se"].home).data["server"]
    # Map a public test hostname to the real HTTP fixture; no real DNS/tunnel is used.
    settings = p["sa"].computers
    session = settings.http
    settings.http = SimpleNamespace(
        get=lambda url, **kw: session.get(address.rstrip("/") + URL(url).raw_path, **kw),
        ws_connect=lambda url, **kw: session.ws_connect(address.rstrip("/") + URL(url).raw_path, **kw))
    try:
        r = await p["server"].post("/api/computers/cloudflare/verify", json={"url": "https://ken.example.com"})
        assert r.status == 200
        assert not (await r.json())["local_only"]
        assert Computers(p["se"].home).data["server"]["url"] == "https://ken.example.com"
    finally:
        settings.http = session
    # Even if someone reused a key, a different Ken instance must not pass this check.
    await p["client"].put("/api/computers/server", json={"enabled": True})
    p["ca"].computers.data["server"]["key"] = p["key"]
    r = await p["server"].post("/api/computers/cloudflare/verify", json={"url": str(p["client"].make_url(""))})
    assert r.status == 400 and "different Ken" in (await r.json())["error"]
    await p["server"].put("/api/computers/server", json={"enabled": False})
    assert (await p["server"].post("/api/computers/cloudflare/verify", json={"url": address})).status == 400


async def test_tunnel_verification_access_headers_redirects_and_no_secret_storage(pair, aiohttp_client):
    p = pair
    seen = []
    mode = "ok"
    async def identity(request):
        seen.append(dict(request.headers))
        if mode == "redirect":
            raise web.HTTPFound("/should-not-follow")
        return web.json_response({"product": "ken", "protocol": 1, "server_id": p["sa"].computers.data["server"]["id"]})
    async def events(request):
        seen.append(dict(request.headers))
        if mode == "blocked":
            raise web.HTTPForbidden()
        if mode == "ws_redirect":
            raise web.HTTPFound("/should-not-follow")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "hello"})
        async for _ in ws:
            pass
        return ws
    async def unexpected(request):
        pytest.fail("Must not follow a redirect with credentials")
    app = web.Application()
    app.router.add_get("/api/identity", identity)
    app.router.add_get("/api/events", events)
    app.router.add_get("/should-not-follow", unexpected)
    tunnel = await aiohttp_client(app)
    payload = {"url": str(tunnel.make_url("")), "access_id": "access-client", "access_secret": "access-secret"}
    r = await p["server"].post("/api/computers/cloudflare/verify", json=payload)
    assert r.status == 200
    assert len(seen) == 2
    for headers in seen:
        assert headers["CF-Access-Client-Id"] == "access-client"
        assert headers["CF-Access-Client-Secret"] == "access-secret"
        assert headers["Authorization"] == "Bearer " + p["key"]
        assert "Cookie" not in headers
    assert "access-secret" not in p["sa"].computers.path.read_text()
    for mode in ("blocked", "redirect", "ws_redirect"):
        r = await p["server"].post("/api/computers/cloudflare/verify", json=payload)
        assert r.status == 400, mode
    payload["access_id"] = ""
    assert (await p["server"].post("/api/computers/cloudflare/verify", json=payload)).status == 400


async def test_client_does_not_need_local_claude_or_server_setup(pair):
    p = pair
    p["ca"].setup = SimpleNamespace(status=lambda: {"connected": False, "phone": "declined", "voice": {"state": "ready"}})
    assert (await (await p["client"].get("/api/setup")).json())["connected"] is False
    assert (await (await p["client"].get(p["prefix"] + "/api/setup")).json())["connected"] is True
    assert p["ca"].computers.data["server"]["enabled"] is False
    assert (await p["client"].post("/api/computers/select", json={"id": p["computer"]["id"]})).status == 200


async def test_remote_actions_files_and_selection_stay_on_their_computer(pair):
    p = pair
    base = p["prefix"]
    c = p["client"]
    agent = await (await c.post(base + "/api/chats", json={"name": "Server project"})).json()
    assert agent["id"] == "server-project"
    assert len(p["se"].list_agents()) == 1 and not p["ce"].list_agents()
    form = aiohttp.FormData()
    form.add_field("file", b"remote file contents", filename="notes.txt", content_type="text/plain")
    r = await c.post(base + "/api/chats/server-project/files?attach=1", data=form)
    assert r.status == 200, await r.text()
    saved = (await r.json())["saved"][0]
    name = saved if isinstance(saved, str) else saved["name"]
    r = await c.get(base + "/api/chats/server-project/files/" + name)
    assert r.headers["Content-Security-Policy"] == "sandbox; default-src 'none'"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert await r.read() == b"remote file contents"
    r = await c.get(base + "/api/chats/server-project/files/" + name, headers={"Range": "bytes=0-5"})
    assert r.status == 206 and await r.read() == b"remote"
    assert (await c.get("/api/chats/server-project")).status == 404
    # Selection only changes the next page, never the destination of an API call.
    selected = await c.post("/api/computers/select", json={"id": p["computer"]["id"]})
    assert selected.cookies["ken_computer"]["httponly"]
    assert (await c.get("/", allow_redirects=False)).headers["Location"].startswith("/computer/")
    assert (await c.get("/api/chats/server-project")).status == 404
    assert (await c.get(base + "/api/chats/server-project")).status == 200
    await c.post("/api/computers/select", json={"id": "local"})
    assert (await c.get("/", allow_redirects=False)).status == 200
    # No secrets in listings or page HTML.
    listing = await (await c.get("/api/computers")).text()
    assert p["key"] not in listing and "access_secret" not in listing
    # Encoding the ID must not shift the raw path slicing into the destination host.
    cid = p["computer"]["id"]
    path = "/remote/%" + format(ord(cid[0]), "02x") + cid[1:] + "/api/meta"
    assert (await c.get(URL(path, encoded=True))).status == 400


async def test_lost_client_does_not_stop_server_run_and_reconnect_recovers(pair, aiohttp_client):
    p = pair
    started, release = asyncio.Event(), asyncio.Event()

    class WaitingSession(FakeSession):
        async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
            started.set()
            await release.wait()
            await on_text("Finished while you were away")
            return "Finished while you were away"

    class WaitingBrain(FakeBrain):
        def session(self, **kw):
            return WaitingSession(self, resume=None, **kw)

    p["se"].brain = WaitingBrain()
    p["se"].create_agent("Overnight")
    ws = await p["client"].ws_connect(p["prefix"] + "/api/events")
    assert (await ws.receive_json())["type"] == "hello"
    r = await p["client"].post(p["prefix"] + "/api/runs", json={"agent": "overnight", "prompt": "Keep working"})
    run = await r.json()
    await asyncio.wait_for(started.wait(), 2)
    await p["client"].close()  # shuts down the entire client gateway, not just its window
    assert p["se"].get_run(run["id"])["status"] == "running"
    release.set()
    await asyncio.wait_for(p["se"].run_tasks[run["id"]], 2)
    _, reconnected = await client_for(p["ce"], aiohttp_client)
    recovered = await (await reconnected.get(p["prefix"] + "/api/runs/" + run["id"])).json()
    assert recovered["status"] == "done"
    assert recovered["summary"] == "Finished while you were away"
    assert not p["ce"].list_runs()


async def test_revocation_closes_live_stream_and_blocks_old_key(pair):
    p = pair
    ws = await p["client"].ws_connect(p["prefix"] + "/api/events")
    assert (await ws.receive_json())["type"] == "hello"
    p["se"].emit("test.event", project="home", text="hello")
    assert (await ws.receive_json())["text"] == "hello"
    rotated = await (await p["server"].put("/api/computers/server", json={"rotate": True})).json()
    assert rotated["key"] != p["key"]
    assert (await asyncio.wait_for(ws.receive(), 2)).type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
    r = await p["client"].get(p["prefix"] + "/api/meta")
    assert r.status == 502 and "key" in (await r.json())["error"]
    await p["server"].put("/api/computers/server", json={"enabled": False})
    assert (await p["server"].get("/api/meta", headers={"Authorization": "Bearer " + rotated["key"], "X-Ken-Remote": "1"})).status == 401
    assert (await p["server"].get("/api/meta")).status == 200


async def test_offline_and_removed_servers_never_fall_back_to_local(pair):
    p = pair
    await p["server"].close()
    r = await p["client"].post(p["prefix"] + "/api/chats", json={"name": "Must not run here"})
    assert r.status == 502
    assert not p["ce"].list_agents()
    check = await (await p["client"].post("/api/computers/" + p["computer"]["id"] + "/check", json={})).json()
    assert check["online"] is False
    await p["client"].delete("/api/computers/" + p["computer"]["id"])
    r = await p["client"].post(p["prefix"] + "/api/chats", json={"name": "Still not local"})
    assert r.status == 404 and not p["ce"].list_agents()


async def test_remove_closes_stream_without_disabling_server(pair):
    p = pair
    ws = await p["client"].ws_connect(p["prefix"] + "/api/events")
    await ws.receive_json()
    await p["client"].delete("/api/computers/" + p["computer"]["id"])
    assert (await asyncio.wait_for(ws.receive(), 2)).type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
    assert p["sa"].computers.data["server"]["enabled"] is True


async def test_stop_remote_queued_work(pair):
    p = pair
    p["se"].create_agent("Queue")
    p["se"].run_slots = asyncio.Semaphore(0)
    run = await (await p["client"].post(p["prefix"] + "/api/runs", json={"agent": "queue", "prompt": "Wait"})).json()
    r = await p["client"].post(p["prefix"] + "/api/runs/" + run["id"] + "/stop", json={})
    assert r.status == 200
    assert p["se"].get_run(run["id"])["status"] == "stopped"
    assert not p["ce"].list_runs()


async def test_retry_after_lost_response_does_not_duplicate_message(pair):
    p = pair
    body = {"text": "Do this once", "client_id": "stable-client-id"}
    for _ in range(2):
        assert (await p["client"].post(p["prefix"] + "/api/chats/home/messages", json=body)).status == 200
    await asyncio.gather(*p["sa"].bg)
    assert len(p["se"].brain.asks) == 1
    assert not p["ce"].brain.asks


async def test_access_service_headers_and_no_redirects(tmp_path, aiohttp_client):
    seen = []

    async def identity(request):
        seen.append(dict(request.headers))
        return web.json_response({"product": "ken", "protocol": 1, "name": "Access Mac"})

    async def redirect(request):
        raise web.HTTPFound("/login")

    async def events(request):
        seen.append(dict(request.headers))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "hello"})
        async for _ in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/api/identity", identity)
    app.router.add_get("/api/events", events)
    app.router.add_get("/api/redirect-events", redirect)
    app.router.add_get("/api/meta", redirect)
    target = await aiohttp_client(app)
    _, c = await client_for(make_engine(tmp_path / "client"), aiohttp_client)
    r = await c.post("/api/computers", json={"url": str(target.make_url("")), "key": "ken-secret", "access_id": "cf-id", "access_secret": "cf-secret"})
    assert r.status == 200
    saved = await r.json()
    assert seen[0]["CF-Access-Client-Id"] == "cf-id"
    assert seen[0]["CF-Access-Client-Secret"] == "cf-secret"
    assert seen[0]["Authorization"] == "Bearer ken-secret"
    assert "Cookie" not in seen[0]
    ws = await c.ws_connect("/remote/" + saved["id"] + "/api/events")
    assert (await ws.receive_json())["type"] == "hello"
    assert seen[-1]["CF-Access-Client-Id"] == "cf-id"
    assert seen[-1]["CF-Access-Client-Secret"] == "cf-secret"
    await ws.close()
    assert (await c.get("/remote/" + saved["id"] + "/api/meta")).status == 502
    with pytest.raises(aiohttp.WSServerHandshakeError):
        await c.ws_connect("/remote/" + saved["id"] + "/api/redirect-events")
    assert "cf-secret" not in await (await c.get("/api/computers")).text()
