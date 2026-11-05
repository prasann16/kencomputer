"""Messages sent in a quick burst ("Apply here", then the link) reach Ken as one turn."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot


def update(chat=1, mid=1):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_message=SimpleNamespace(message_id=mid))


@pytest.fixture
def gather(monkeypatch):
    g = bot.Gather(0.05, 0.3)
    monkeypatch.setattr(bot, "GATHER", g)
    monkeypatch.setattr(bot.ENGINE, "chat_busy", lambda pid: False)
    handle = AsyncMock()
    monkeypatch.setattr(bot, "handle_prompt", handle)
    return g, handle


async def test_burst_becomes_one_turn(gather):
    _, handle = gather
    first, second = update(mid=1), update(mid=2)
    a = asyncio.create_task(bot.gathered(first, "Apply here", "(They are replying to your earlier message)\n"))
    await asyncio.sleep(0.02)
    await bot.gathered(second, "https://jobs.example.com/42")
    await a
    handle.assert_awaited_once()
    (upd, prompt, context), kw = handle.await_args.args, handle.await_args.kwargs
    assert upd is first and context.startswith("(They are replying")
    assert prompt.index("Apply here") < prompt.index("https://jobs.example.com/42")
    assert kw["said"] == "Apply here\n\nhttps://jobs.example.com/42"


async def test_attachments_kept_in_order(gather):
    _, handle = gather
    a = asyncio.create_task(bot.gathered(update(), "(image a)", said="", files=["a.jpg"]))
    await asyncio.sleep(0.02)
    await bot.gathered(update(), "(image b)", said="look at these", files=["b.jpg"])
    await a
    kw = handle.await_args.kwargs
    assert kw["files"] == ["a.jpg", "b.jpg"] and kw["said"] == "look at these"


async def test_lone_message_unchanged(gather):
    _, handle = gather
    u = update()
    await bot.gathered(u, "hi", "")
    handle.assert_awaited_once_with(u, "hi", "", said=None, files=None)


async def test_quiet_spell_ends_burst(gather):
    _, handle = gather
    await bot.gathered(update(), "one")
    await bot.gathered(update(), "two")  # after a quiet spell: its own turn
    assert handle.await_count == 2


async def test_wait_is_capped(gather):
    g, _ = gather
    loop = asyncio.get_running_loop()
    start = loop.time()
    a = asyncio.create_task(g.add(1, "first"))
    await asyncio.sleep(0)
    while g.waiting(1):  # someone who never stops: a message every 30 ms
        await g.add(1, "more")
        await asyncio.sleep(0.03)
    assert loop.time() - start < 0.5
    assert (await a)[0] == "first"


async def test_poke_extends_the_wait(gather):
    g, _ = gather
    a = asyncio.create_task(g.add(1, "x"))
    for _ in range(4):  # e.g. a typing signal, every 30 ms, longer than one quiet spell in total
        await asyncio.sleep(0.03)
        assert not a.done()
        g.poke(1)
    assert await a == ["x"]


async def test_chats_kept_apart(gather):
    _, handle = gather
    await asyncio.gather(bot.gathered(update(chat=1), "a"), bot.gathered(update(chat=2), "b"))
    assert handle.await_count == 2


async def test_busy_ken_takes_it_straight_away(gather, monkeypatch):
    _, handle = gather
    monkeypatch.setattr(bot.ENGINE, "chat_busy", lambda pid: True)
    u = update()
    await asyncio.wait_for(bot.gathered(u, "also this"), 0.02)
    handle.assert_awaited_once_with(u, "also this", "", said=None, files=None)


async def test_off_when_zero(gather, monkeypatch):
    _, handle = gather
    monkeypatch.setattr(bot, "GATHER", bot.Gather(0, 12))
    await asyncio.wait_for(bot.gathered(update(), "now"), 0.02)
    handle.assert_awaited_once()
