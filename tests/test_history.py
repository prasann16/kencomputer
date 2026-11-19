"""Looking back: Clear can be undone, a cleared chat's earlier messages load a page at a time, every file that passed
through the chat is listed, and each day's conversation can be read again. One engine answers the web app and the phone."""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import HOME  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401


async def test_undo_clear_brings_the_thread_back(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    engine.log_message(HOME, "you", "plan my trip to Porto")
    engine.log_message(HOME, "ken", "Here's a plan.")
    assert (await client.post(f"/api/chats/{HOME}/reset", json={})).status == 200
    await asyncio.gather(*engine._bg)
    assert (await (await client.get(f"/api/chats/{HOME}")).json())["messages"] == []

    r = await client.post(f"/api/chats/{HOME}/unclear", json={})
    assert r.status == 200
    view = await r.json()
    assert [m["text"] for m in view["messages"]] == ["plan my trip to Porto", "Here's a plan."]
    assert view["cleared_at"] == 0  # as it was: never cleared
    assert engine.kv_get(f"primer:{HOME}") == "1"  # the AI's next turn hears the restored thread
    assert (await client.post(f"/api/chats/{HOME}/unclear", json={})).status == 409  # only the latest Clear


async def test_undo_goes_back_to_the_clear_before(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    engine.log_message(HOME, "ken", "first")
    await engine.reset_chat(HOME, clear=True)
    first_clear = engine.chat_view(HOME)["cleared_at"]
    engine.log_message(HOME, "ken", "second")
    await engine.reset_chat(HOME, clear=True)
    await asyncio.gather(*engine._bg)
    view = await (await client.post(f"/api/chats/{HOME}/unclear", json={})).json()
    assert [m["text"] for m in view["messages"]] == ["second"] and view["cleared_at"] == first_clear


async def test_earlier_messages_page_back_from_the_clear(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    for i in range(5):
        engine.log_message(HOME, "you", f"old {i}")
    await engine.reset_chat(HOME, clear=True)
    await asyncio.gather(*engine._bg)
    engine.log_message(HOME, "you", "new")

    page = await (await client.get(f"/api/chats/{HOME}/earlier?limit=3")).json()
    assert [m["text"] for m in page["messages"]] == ["old 2", "old 3", "old 4"] and page["more"]
    assert {"id", "ts", "project", "role", "text"} <= set(page["messages"][0])
    page = await (await client.get(f"/api/chats/{HOME}/earlier?limit=3&before={page['messages'][0]['id']}")).json()
    assert [m["text"] for m in page["messages"]] == ["old 0", "old 1"] and not page["more"]
    # never the current thread, whatever `before` says
    page = await (await client.get(f"/api/chats/{HOME}/earlier?before=999999")).json()
    assert "new" not in [m["text"] for m in page["messages"]]


async def test_files_that_passed_through_the_chat(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    work = engine.home_cwd
    work.mkdir(parents=True, exist_ok=True)
    (work / "reel.mp4").write_bytes(b"v" * 10)
    (work / "notes.pdf").write_bytes(b"p" * 5)
    (work / "scratch.png").write_bytes(b"x")  # Ken made it but never named it
    inbox = engine.files_dir(HOME)
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "photo.jpg").write_bytes(b"j" * 3)

    engine.log_message(HOME, "you", "here's a photo", files=["photo.jpg", "gone.png"])
    engine.log_message(HOME, "ken", f"Your reel: `{work / 'reel.mp4'}` and the notes [notes]({work / 'notes.pdf'}). Also /nope/missing.mp4")
    engine.log_message(HOME, "ken", f"Same reel again: {work / 'reel.mp4'}")

    files = (await (await client.get("/api/files?kind=all&pid=home")).json())["files"]
    by_name = {f["name"]: f for f in files}
    assert set(by_name) == {"reel.mp4", "notes.pdf", "photo.jpg"}  # deduplicated, missing and unnamed files left out
    assert [f["name"] for f in files][0] == "reel.mp4"  # newest first
    assert by_name["reel.mp4"]["kind"] == "video" and by_name["reel.mp4"]["size"] == 10 and by_name["reel.mp4"]["from"] == "ken"
    assert by_name["photo.jpg"]["kind"] == "picture" and by_name["photo.jpg"]["from"] == "you"
    assert by_name["notes.pdf"]["kind"] == "doc"
    assert Path(by_name["reel.mp4"]["path"]) == (work / "reel.mp4").resolve()
    assert {"path", "name", "kind", "size", "mtime", "from", "ts"} <= set(by_name["reel.mp4"])

    videos = (await (await client.get("/api/files?kind=video")).json())["files"]
    assert [f["name"] for f in videos] == ["reel.mp4"]
    assert (await client.get("/api/files?kind=nope")).status == 400
    assert (await client.get("/api/files?pid=no-such-agent")).status == 404


async def test_conversations_by_day(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    engine.log_message(HOME, "you", "find me cheap flights to New York")
    engine.log_message(HOME, "ken", "Here are three.")
    engine.log_message(HOME, "you", "book the second one please, and send me the confirmation when it's done")
    yesterday = time.time() - 86400
    engine.log_message(HOME, "ken", "Morning brief", job={"label": "Morning brief"})
    engine.db.execute("UPDATE events SET ts=? WHERE id=(SELECT MAX(id) FROM events)", (yesterday,))

    days = (await (await client.get("/api/days?pid=home")).json())["days"]
    today = time.strftime("%Y-%m-%d")
    assert [d["date"] for d in days] == [today, time.strftime("%Y-%m-%d", time.localtime(yesterday))]
    assert days[0]["count"] == 3
    assert days[0]["summary"].startswith("find me cheap flights to New York · book the second") and len(days[0]["summary"]) <= 80
    assert days[1]["summary"] == "Morning brief"

    messages = (await (await client.get(f"/api/days/{today}?pid=home")).json())["messages"]
    assert [m["role"] for m in messages] == ["you", "ken", "you"] and messages[1]["text"] == "Here are three."
    assert (await client.get("/api/days/not-a-day")).status == 400
