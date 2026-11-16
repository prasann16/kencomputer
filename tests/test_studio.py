"""HyperFrames Studio behind Ken's web app: an edit link opens the editor for one of Ken's video projects, everything
Studio asks for passes through (its live updates stream), and nothing outside videos/<project> can be opened."""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import web  # noqa: E402

import studio  # noqa: E402
from engine import HOME  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_the_editor_opens_for_a_ken_video_project_and_streams_through(engine, aiohttp_client, monkeypatch):  # noqa: F811
    port = free_port()
    monkeypatch.setattr(studio, "PORT", port)
    started = []

    async def ensure(project):
        started.append(project.name)
    monkeypatch.setattr(studio, "ensure", ensure)

    # A stand-in for Studio itself: its page, an API call, and its live updates (server-sent events).
    async def page(request):
        return web.Response(text="<title>HyperFrames Studio</title>", content_type="text/html")

    async def projects(request):
        return web.json_response({"projects": ["reel"], "q": request.query.get("q", "")})

    async def events(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for n in range(3):
            await resp.write(f"data: {n}\n\n".encode())
            await asyncio.sleep(0.01)
        return resp

    fake = web.Application()
    fake.router.add_get("/", page)
    fake.router.add_get("/api/projects", projects)
    fake.router.add_get("/api/events", events)
    runner = web.AppRunner(fake)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    try:
        videos = engine.home_cwd / "videos"
        (videos / "reel").mkdir(parents=True)
        (videos / "reel" / "hyperframes.json").write_text("{}")
        (videos / "notes").mkdir()  # not a HyperFrames project
        (videos / "porto" / "edit").mkdir(parents=True)  # what the video skill makes: the project in edit/
        (videos / "porto" / "edit" / "hyperframes.json").write_text("{}")
        app, client = await signed_in(engine, aiohttp_client)
        assert (await client.post("/api/studio", json={"project": "reel"})).status == 200
        assert started == ["reel"]
        assert (await client.post("/api/studio", json={"project": "porto"})).status == 200
        assert started == ["reel", "edit"]  # videos/porto/edit
        for bad in ("notes", "../work", "", "nope"):
            assert (await client.post("/api/studio", json={"project": bad})).status == 404
        assert "HyperFrames Studio" in await (await client.get("/studio/")).text()
        assert (await (await client.get("/studio/api/projects?q=x")).json()) == {"projects": ["reel"], "q": "x"}
        stream = await client.get("/studio/api/events")
        assert stream.headers["Content-Type"].startswith("text/event-stream")
        assert await stream.text() == "data: 0\n\ndata: 1\n\ndata: 2\n\n"
        client.session.cookie_jar.clear()
        assert (await client.get("/studio/", headers={"Authorization": "Bearer wrong"})).status == 401  # Ken's own lock
    finally:
        await runner.cleanup()


def test_the_edit_link_is_on_the_host_behind_sign_in(monkeypatch):
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://kencomputer.dev/box/checkin")
    assert studio.link("porto-reel") == "https://kencomputer.dev/studio/porto-reel"
    monkeypatch.delenv("KEN_KEEPALIVE_URL")
    assert studio.link("porto-reel") == ""
    monkeypatch.setenv("KEN_STUDIO_SITE", "http://localhost:4600/")  # a stand-in for the host, in a local test
    assert studio.link("porto-reel") == "http://localhost:4600/studio/porto-reel"
