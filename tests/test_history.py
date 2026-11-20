"""Looking back: a cleared chat's earlier messages load a page at a time. One engine answers the web app and the phone."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import HOME  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401


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
