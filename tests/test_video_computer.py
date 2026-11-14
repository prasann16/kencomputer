"""Hosted Ken can keep an edited video on the person's own computer instead of their Drive: Ken's page open there pulls
each file through the same one-file link, sends back its size and SHA-256, and only a match deletes the copy here.
Nothing on their computer listens; phones and Telegram get Drive; with neither, the files stay and Ken says so."""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drive_store  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401
from test_video_drive import home, project  # noqa: E402,F401

CHROME = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_offer_lists_each_file_with_what_the_copy_must_match(home, project):  # noqa: F811
    offered = drive_store.offer(project)
    assert [o["kind"] for o in drive_store.pickups()] == ["reel", "source"] == [o["pickup"] for o in offered]
    reel = next(p for p in drive_store.pickups() if p["kind"] == "reel")
    assert reel["sha256"] == sha((project / "reel.mp4").read_bytes()) and reel["size"] == (project / "reel.mp4").stat().st_size
    assert "path" not in reel  # the page never sees where files are here
    assert drive_store.linked_file(reel["token"]) == (project / "reel.mp4").resolve()  # the same link Drive uses
    drive_store.offer(project)  # offered again: the old links are replaced, not added to
    assert len(drive_store.pickups()) == 2


def test_only_a_matching_copy_deletes_the_file_here(home, project):  # noqa: F811
    offered = {o["pickup"]: o for o in drive_store.offer(project)}
    reel = offered["reel"]
    assert not drive_store.picked_up(reel["token"], reel["size"] - 1, reel["sha256"])["ok"]  # cut short
    assert not drive_store.picked_up(reel["token"], reel["size"], "0" * 64)["ok"]  # different bytes
    assert not drive_store.picked_up("wrong", reel["size"], reel["sha256"])["ok"]
    assert (project / "reel.mp4").exists() and len(drive_store.pickups()) == 2
    assert drive_store.picked_up(reel["token"], reel["size"], reel["sha256"].upper(), "disk", "reel.mp4")["ok"]
    assert not (project / "reel.mp4").exists() and (project / "cut.mp4").exists()  # cut.mp4 waits for the original
    src = offered["source"]
    assert drive_store.picked_up(src["token"], src["size"], src["sha256"], "download")["ok"]
    assert not (project / "source.mov").exists() and not (project / "cut.mp4").exists()
    assert (project / "props.json").exists() and (project / "source.words.json").exists()
    note = json.loads((project / "drive.json").read_text())
    assert note["reel"]["where"] == note["source"]["where"] == "computer" and note["reel"]["checked"] == "disk"
    assert drive_store.pickups() == [] and drive_store._links() == {}
    assert not drive_store.picked_up(reel["token"], reel["size"], reel["sha256"])["ok"]  # used once


def test_an_expired_offer_keeps_the_file(home, project):  # noqa: F811
    reel = drive_store.offer(project, now=1000)[0]
    assert drive_store.pickups(now=1000 + drive_store.LINK_TTL_S + 1) == []
    assert "expired" in drive_store.picked_up(reel["token"], reel["size"], reel["sha256"], now=1000 + drive_store.LINK_TTL_S + 1)["error"]
    assert (project / "reel.mp4").exists()


async def test_the_page_pulls_checks_and_confirms(engine, aiohttp_client, home, project):  # noqa: F811
    """What Ken's page does in their browser, step by step, against the real web app."""
    app, client = await signed_in(engine, aiohttp_client)
    reel_bytes = (project / "reel.mp4").read_bytes()
    drive_store.offer(project, with_source=False)
    waiting = (await (await client.get("/api/pickups")).json())["pickups"]
    assert [p["name"] for p in waiting] == ["reel.mp4"]
    p = waiting[0]
    got = await (await client.get(f"/api/drive-file/{p['token']}")).read()
    assert got == reel_bytes
    r = await client.post(f"/api/pickups/{p['token']}", json={"size": len(got) - 5, "sha256": sha(got[:-5]), "how": "disk"})
    assert r.status == 409 and (project / "reel.mp4").exists()
    q = engine.subscribe()
    r = await client.post(f"/api/pickups/{p['token']}", json={"size": len(got), "sha256": sha(got), "how": "disk", "where": "reel.mp4"})
    assert r.status == 200 and (await r.json())["ok"]
    assert not (project / "reel.mp4").exists() and (project / "source.mov").exists()
    assert (await asyncio.wait_for(q.get(), 1))["type"] == "pickup"  # open pages drop the bar
    assert (await (await client.get("/api/pickups")).json())["pickups"] == []
    assert (await client.get(f"/api/drive-file/{p['token']}")).status == 404
    client.session.cookie_jar.clear()  # nobody else can list, pull or confirm
    assert (await client.get("/api/pickups")).status == 401


async def test_ken_knows_whether_its_page_is_open_on_a_computer(engine, aiohttp_client, home):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    pages = lambda: client.get("/api/pickups/pages")  # noqa: E731
    assert await (await pages()).json() == {"computer": 0, "phone": 0}
    phone = await client.ws_connect("/api/events", headers={"User-Agent": IPHONE})
    await phone.receive_json()
    assert await (await pages()).json() == {"computer": 0, "phone": 1}
    laptop = await client.ws_connect("/api/events", headers={"User-Agent": CHROME})
    await laptop.receive_json()
    assert await (await pages()).json() == {"computer": 1, "phone": 1}
    q = engine.subscribe()
    assert (await client.post("/api/pickups/announce", json={})).status == 200
    assert (await asyncio.wait_for(q.get(), 1))["type"] == "pickup"
    await laptop.close()
    await asyncio.sleep(0.05)
    assert (await (await pages()).json())["computer"] == 0
    await phone.close()


def test_save_goes_to_their_computer_then_drive_then_stays(home, project, monkeypatch):  # noqa: F811
    went = []
    monkeypatch.setattr(drive_store, "_web", lambda path, body=None, timeout=5: went.append(path) or {})
    monkeypatch.setattr(drive_store, "save", lambda folder, src, account, say: went.append("drive") or 0)
    said = []
    monkeypatch.setattr(drive_store, "computer_open", lambda: 1)
    monkeypatch.setattr(drive_store, "connected", lambda: {"ok": True, "account": ""})
    assert drive_store.save_where(project, "auto", True, "", said.append) == 0
    assert went == ["/api/pickups/announce"] and "tap Save" in said[-1] and (project / "reel.mp4").exists()
    went.clear()
    assert drive_store.save_where(project, "drive", True, "", said.append) == 0 and went == ["drive"]  # they said Drive
    went.clear()
    monkeypatch.setattr(drive_store, "computer_open", lambda: 0)  # on their phone, or nobody's looking
    assert drive_store.save_where(project, "auto", True, "", said.append) == 0 and went == ["drive"]
    went.clear()
    assert drive_store.save_where(project, "computer", True, "", said.append) == 0 and went == ["drive"]
    assert "isn't open on their computer" in said[-1]
    went.clear()
    monkeypatch.setattr(drive_store, "connected", lambda: {"ok": False, "connect_url": "https://x.dev/connect/googledrive"})
    assert drive_store.save_where(project, "auto", True, "", said.append) == 1 and went == []
    assert "stays here" in said[-1] and "https://x.dev/connect/googledrive" in said[-1]
    assert (project / "reel.mp4").exists() and (project / "source.mov").exists()


def test_check_lets_the_edit_start_when_their_computer_is_there(home, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setenv("KEN_DRIVE_FAKE", str(home / "drive"))
    monkeypatch.setattr(drive_store, "connected", lambda: {"ok": False, "connect_url": "https://x.dev/c"})
    monkeypatch.setattr(drive_store, "computer_open", lambda: 1)
    assert drive_store.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "go ahead" in out and "https://x.dev/c" not in out
    monkeypatch.setattr(drive_store, "computer_open", lambda: 0)
    assert drive_store.main(["check"]) == 0
    assert "https://x.dev/c" in capsys.readouterr().out


def test_computer_open_is_zero_without_a_running_app(home, monkeypatch):  # noqa: F811
    monkeypatch.setenv("KEN_WEB_PORT", "1")  # nothing there
    (home / "web-token").write_text("t" * 30)
    assert drive_store.computer_open() == 0


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="needs node")
def test_page_only_shows_the_save_bar_on_a_computer():
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    assert "function SavePickups()" in js and "if (PHONE_PAGE || !shown.length) return null;" in js
    assert "/api/drive-file/${p.token}" in js and "crypto.subtle.digest('SHA-256'" in js
    assert "showSaveFilePicker" in js and "handle.getFile()" in js  # read back from disk before confirming
