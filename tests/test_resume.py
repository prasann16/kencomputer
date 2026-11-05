"""A reply cut off by a restart never leaves the chat hanging: Ken says so in one line and carries on."""
import asyncio
import time

import pytest

from engine import HOME, Engine
from test_engine import FakeBrain, FakeSession


@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path, FakeBrain())
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    e.agents_dir.mkdir()
    e.requests_dir.mkdir()
    return e


class SlowSession(FakeSession):
    """A reply that's still going (posting something, say) when the computer restarts."""
    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        self.brain.asks.append({"prompt": prompt})
        self.busy = True
        await on_text("Adding the line at the bottom, then posting.")
        await asyncio.sleep(3600)


async def test_a_finished_reply_leaves_no_note(engine):
    await engine.send_message(HOME, "plan my day")
    assert not (engine.home / "turns-in-progress.json").exists()
    assert await engine.resume_interrupted() == []


async def test_a_reply_cut_off_by_a_restart_is_picked_up_with_one_line_first(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine.brain, "session", lambda **kw: SlowSession(engine.brain, kw["cwd"], kw["system"], kw.get("resume")))
    task = asyncio.create_task(engine.send_message(HOME, "add the line to the doc and post it", via="telegram"))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.brain.asks:
            break
    assert (engine.home / "turns-in-progress.json").exists()  # on disk while it runs
    await engine.close()  # the computer restarts: Ken shuts down mid-reply
    assert task.done()
    assert (engine.home / "turns-in-progress.json").exists()  # kept for the next start

    # The next start: a fresh engine on the same folder, with the usual test brain.
    after = Engine(tmp_path, FakeBrain())
    after.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    told = []

    async def to_telegram(text):
        told.append(text)
    after.resume_listener = lambda via: to_telegram if via == "telegram" else None
    assert await after.resume_interrupted() == [HOME]
    await asyncio.gather(*after._bg)
    prompt = after.brain.asks[-1]["prompt"]
    assert 'Sorry, I was interrupted. Picking up where I left off.' in prompt
    assert '"add the line to the doc and post it"' in prompt
    assert "ask them before doing it again instead of repeating it" in prompt  # posting: never blindly twice
    assert told == ["did it"]  # the carried-on reply goes back to Telegram too
    assert not (tmp_path / "turns-in-progress.json").exists()
    assert await after.resume_interrupted() == []  # once


async def test_a_reply_they_stopped_isnt_picked_up_again(engine, monkeypatch):
    monkeypatch.setattr(engine.brain, "session", lambda **kw: SlowSession(engine.brain, kw["cwd"], kw["system"], kw.get("resume")))
    task = asyncio.create_task(engine.send_message(HOME, "write it"))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.brain.asks:
            break
    task.cancel()  # Stop, by them (Ken isn't shutting down)
    await asyncio.gather(task, return_exceptions=True)
    assert not (engine.home / "turns-in-progress.json").exists()


async def test_a_turn_cut_off_long_ago_or_in_a_removed_agent_is_left(engine):
    engine._write_turns({HOME: {"since": time.time() - 7 * 3600, "asked": "old"}, "gone-agent": {"since": time.time(), "asked": "x"}})
    assert await engine.resume_interrupted() == []
    assert not (engine.home / "turns-in-progress.json").exists()
