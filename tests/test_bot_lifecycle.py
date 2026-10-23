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
    engine, phone = SimpleNamespace(close=AsyncMock()), SimpleNamespace(stop=AsyncMock())
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
    assert (tmp_path / "codex").stat().st_mode & 0o077 == 0  # the login lives here: only its owner can read it
