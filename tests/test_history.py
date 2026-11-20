"""Looking back: a Clear can be undone (nothing in a chat is ever deleted). One engine answers the web app and the phone."""
import asyncio
import os
import sys
import tempfile
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
