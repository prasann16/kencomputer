"""A reel's live preview: the computer serves its page, the player code, props.json and only the files props.json
names (streamed with Range), so a link plays the reel in a browser with nothing rendered. Nothing else in the folder."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("KEN_HOME", tempfile.mkdtemp())  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import video_kit  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401


def reel(engine, name="left-sf"):
    folder = engine.home_cwd / "videos" / name
    (folder / ".preview").mkdir(parents=True)
    (folder / "preview.html").write_text("<!doctype html><title>Reel preview</title>")
    (folder / ".preview" / "preview.js").write_text("console.log(1)")
    (folder / "props.json").write_text(json.dumps({"video": "cut.mp4", "graphics": [{"type": "end", "props": {"src": "logo.png"}}]}))
    (folder / "cut.mp4").write_bytes(bytes(range(256)) * 40)
    (folder / "logo.png").write_bytes(b"\x89PNG")
    (folder / "source.mov").write_bytes(b"raw")
    (folder / "plan.md").write_text("private notes")
    return folder


async def test_the_preview_serves_its_page_and_streams_the_footage(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    reel(engine)
    page = await client.get("/reel/left-sf/")
    assert page.status == 200 and page.headers["Content-Type"].startswith("text/html")
    assert "script-src" not in page.headers["Content-Security-Policy"] and "default-src 'self'" in page.headers["Content-Security-Policy"]
    assert (await client.get("/reel/left-sf/.preview/preview.js")).headers["Content-Type"].startswith("text/javascript")
    assert (await client.get("/reel/left-sf/props.json")).status == 200
    part = await client.get("/reel/left-sf/cut.mp4", headers={"Range": "bytes=10-19"})
    assert part.status == 206 and await part.read() == bytes(range(10, 20))
    assert (await client.get("/reel/left-sf/logo.png")).status == 200  # a file a graphic shows


async def test_nothing_else_in_the_folder_is_served(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    reel(engine)
    for path in ("/reel/left-sf/source.mov", "/reel/left-sf/plan.md", "/reel/left-sf/../left-sf/plan.md",
                 "/reel/left-sf/.preview/../plan.md", "/reel/.hidden/", "/reel/missing/"):
        assert (await client.get(path)).status == 404, path


async def test_the_preview_needs_ken_s_sign_in(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    reel(engine)
    client.session.cookie_jar.clear()
    assert (await client.get("/reel/left-sf/", allow_redirects=False)).status in (302, 401, 403)


def test_the_link_is_on_the_hosting_site_or_this_computer(monkeypatch, tmp_path):
    folder = tmp_path / "work" / "videos" / "left-sf"
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://kencomputer.dev/box/checkin")
    monkeypatch.delenv("AGENT37_INSTANCE_ID", raising=False)
    assert video_kit.preview_link(tmp_path, folder) == "https://kencomputer.dev/reel/left-sf/"
    monkeypatch.delenv("KEN_KEEPALIVE_URL")
    monkeypatch.setenv("KEN_WEB_PORT", "7788")
    assert video_kit.preview_link(tmp_path, folder) == "http://127.0.0.1:7788/reel/left-sf/"


def test_the_preview_uses_only_packages_every_kit_already_has():
    # Adding a package would reinstall every computer's video tools: the preview runs on what the kit has.
    kit = Path(__file__).resolve().parent.parent / "video" / "kit"
    lock = json.loads((kit / "package-lock.json").read_text())["packages"]
    assert lock["node_modules/esbuild"]["version"] and lock["node_modules/@remotion/player"]["version"] == "4.0.538"
    assert {"preview.mjs", "preview.tsx"} <= set(video_kit.ENGINE)


async def test_a_watch_link_key_opens_only_its_reel_and_keeps_its_link_after_edits(engine, aiohttp_client, monkeypatch):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    reel(engine)
    key = video_kit.share_reel(engine.home, "left-sf")
    assert video_kit.share_reel(engine.home, "left-sf") == key  # the same link after an edit
    page = await client.get(f"/api/reel-shared/{key}/")
    assert page.status == 200 and page.headers["X-Ken-Reel"] == "1" and "frame-ancestors 'self'" in page.headers["Content-Security-Policy"]
    part = await client.get(f"/api/reel-shared/{key}/cut.mp4", headers={"Range": "bytes=0-3"})
    assert part.status == 206 and await part.read() == bytes(range(4))
    assert (await client.get(f"/api/reel-shared/{key}/plan.md")).status == 404
    gone = await client.get("/api/reel-shared/0123456789abcdef01234567/")
    assert gone.status == 404 and (await gone.json())["error"]
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://kencomputer.dev/box/checkin")
    monkeypatch.setenv("AGENT37_INSTANCE_ID", "lmm0bht56u")
    assert video_kit.preview_link(engine.home, engine.home_cwd / "videos" / "left-sf") == f"https://kencomputer.dev/watch/lmm0bht56u/{key}/"
