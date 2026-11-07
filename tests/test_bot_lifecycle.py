"""The engine starts on Windows and drains its work on supported shutdown paths."""
import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import bot


@pytest.mark.parametrize("signal_support,cancel", [(True, False), (True, True), (False, True)],
                         ids=["sigterm", "cancel", "windows-cancel"])
async def test_run_starts_and_cleans_up(monkeypatch, signal_support, cancel):
    loop = asyncio.get_running_loop()
    add_signal = Mock(side_effect=None if signal_support else NotImplementedError)
    remove_signal = Mock()
    monkeypatch.setattr(loop, "set_exception_handler", Mock())
    monkeypatch.setattr(loop, "add_signal_handler", add_signal)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_signal)
    monkeypatch.setattr(bot.crash, "track", Mock())
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    jobs = []

    async def background():
        jobs.append(asyncio.current_task())
        await asyncio.Event().wait()

    web_task = asyncio.create_task(background())
    web = SimpleNamespace(bg={web_task}, runner=SimpleNamespace(cleanup=AsyncMock()))
    engine, phone = SimpleNamespace(close=AsyncMock(), resume_interrupted=AsyncMock(return_value=[])), SimpleNamespace(stop=AsyncMock())
    monkeypatch.setattr(bot, "ENGINE", engine)
    monkeypatch.setattr(bot, "PHONE", phone)
    start = AsyncMock(return_value=web)
    monkeypatch.setattr(bot, "start_engine", start)
    for name in ("scheduler", "self_update", "warmup_voice"):
        monkeypatch.setattr(bot, name, background)
    ready = asyncio.Event()

    async def refresh(**kwargs):
        ready.set()

    monkeypatch.setattr(bot, "refresh_models_file", refresh)
    task = asyncio.create_task(bot.run())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        start.assert_awaited_once()
        assert not task.done()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            sig, callback = add_signal.call_args.args
            assert sig == signal.SIGTERM
            callback()
            await asyncio.wait_for(task, 5)
        assert len(jobs) == 4 and all(job.cancelled() for job in jobs)
        engine.close.assert_awaited_once()
        if not cancel:
            engine.resume_interrupted.assert_awaited_once()  # a reply cut off by the last restart carries on
        phone.stop.assert_awaited_once()
        web.runner.cleanup.assert_awaited_once()
        if signal_support:
            remove_signal.assert_called_once_with(signal.SIGTERM)
        else:
            remove_signal.assert_not_called()
    finally:
        for pending in (task, web_task):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, web_task, return_exceptions=True)


def test_ken_codex_has_its_own_folder(tmp_path, monkeypatch):
    import os
    from engine import prepare_skills
    monkeypatch.setattr(bot, "KEN_HOME", tmp_path)
    monkeypatch.delenv("KEN_CODEX_HOME", raising=False)
    prepare_skills(tmp_path)
    bot.prepare_codex_home(tmp_path / "codex")
    assert os.environ["KEN_CODEX_HOME"] == str(tmp_path / "codex")  # never the person's own ~/.codex
    if os.name != "nt":  # Windows keeps a user's folders private with ACLs, not mode bits
        assert (tmp_path / "codex").stat().st_mode & 0o077 == 0  # the login lives here: only its owner can read it


async def test_kill_stops_everything_and_keeps_ken_off(monkeypatch, tmp_path):
    import time
    note = tmp_path / "off"
    monkeypatch.setattr(bot, "OFF_NOTE", note)
    monkeypatch.setattr(bot, "ALLOWED_USER_ID", 7)
    engine = SimpleNamespace(chats={"home": object()}, stop_chat=AsyncMock(), stop_run=AsyncMock(),
                             list_runs=Mock(return_value=[{"id": "r1"}]))
    monkeypatch.setattr(bot, "ENGINE", engine)
    monkeypatch.setattr(bot, "decaf", Mock())
    off = Mock()
    monkeypatch.setattr(bot, "turn_off", off)

    def tap(sent_at):
        message = SimpleNamespace(date=SimpleNamespace(timestamp=lambda: sent_at), reply_text=AsyncMock())
        return SimpleNamespace(callback_query=SimpleNamespace(from_user=SimpleNamespace(id=7), message=message, answer=AsyncMock(),
                                                              edit_message_reply_markup=AsyncMock()))

    await bot.on_kill_pick(tap(time.time() - 600), None)  # an old button: nothing happens
    assert not note.exists() and not off.called
    await bot.on_kill_pick(tap(time.time()), None)
    assert note.exists() and off.called
    engine.stop_chat.assert_awaited_with("home")
    engine.stop_run.assert_awaited_with("r1", by="you")


def test_an_engine_turned_off_with_kill_doesnt_start(monkeypatch, tmp_path):
    (tmp_path / "off").write_text("now\n")
    monkeypatch.setattr(bot, "OFF_NOTE", tmp_path / "off")
    monkeypatch.setattr(bot, "turn_off", Mock(side_effect=SystemExit))
    lock = Mock()
    monkeypatch.setattr(bot, "one_engine", lock)
    with pytest.raises(SystemExit):
        bot.main()
    assert not lock.called


async def test_screen_only_updates_dont_restart_ken(tmp_path):
    import subprocess
    import bot
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (tmp_path / "web").mkdir(); (tmp_path / "web" / "app.js").write_text("1"); (tmp_path / "engine.py").write_text("1")
    run("add", "."); run("commit", "-qm", "a"); first = run("rev-parse", "HEAD")
    (tmp_path / "web" / "app.js").write_text("2"); (tmp_path / "README.md").write_text("x")
    run("add", "."); run("commit", "-qm", "b"); screens = run("rev-parse", "HEAD")
    assert not await bot.engine_changed(tmp_path, first, screens)
    (tmp_path / "engine.py").write_text("2"); run("add", "."); run("commit", "-qm", "c")
    assert await bot.engine_changed(tmp_path, first, run("rev-parse", "HEAD"))
