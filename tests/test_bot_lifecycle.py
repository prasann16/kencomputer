"""The engine starts on Windows and drains its work on supported shutdown paths."""
import asyncio
import signal
import time
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


async def test_a_screen_only_update_reports_the_new_version(tmp_path, monkeypatch):
    import asyncio
    import subprocess
    import bot
    app = tmp_path / "app"; app.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(app), *a], check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (app / "web").mkdir(); (app / "web" / "app.js").write_text("1"); (app / "engine.py").write_text("1")
    run("add", "."); run("commit", "-qm", "a"); first = run("rev-parse", "HEAD")
    (app / "web" / "app.js").write_text("2"); run("add", "."); run("commit", "-qm", "b"); screens = run("rev-parse", "HEAD")
    monkeypatch.setattr(bot, "KEN_HOME", tmp_path); monkeypatch.setattr(bot, "RUNNING_REV", first)
    monkeypatch.setattr(bot, "FROZEN", False); monkeypatch.delenv("KEN_NO_AUTOUPDATE", raising=False)
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)  # Ken on a computer of its own
    sleeps = []

    async def sleep(s):
        sleeps.append(s)
        if len(sleeps) > 1:
            raise asyncio.CancelledError  # one check is enough
    monkeypatch.setattr(bot.asyncio, "sleep", sleep)
    exits = []
    monkeypatch.setattr(bot.os, "_exit", exits.append)
    try:
        await bot.self_update()
    except asyncio.CancelledError:
        pass
    assert not exits and bot.RUNNING_REV == screens  # no restart, and check-ins say the new version


def test_an_update_never_restarts_ken_right_after_a_message(monkeypatch):
    import bot
    monkeypatch.setattr(bot.ENGINE, "busy", lambda: False)
    now = bot.time.monotonic()
    monkeypatch.setattr(bot, "LAST_INCOMING", now - 30)  # a video came in 30 s ago: still being taken in
    assert bot.restart_would_cut_in()
    monkeypatch.setattr(bot, "LAST_INCOMING", now - bot.HEARD_WAIT - 1)
    assert not bot.restart_would_cut_in()
    monkeypatch.setattr(bot.ENGINE, "busy", lambda: True)
    assert bot.restart_would_cut_in()


async def test_every_telegram_message_is_heard(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "LAST_INCOMING", 0.0)
    await bot.heard(object(), object())
    assert bot.time.monotonic() - bot.LAST_INCOMING < 5


async def test_hosted_ken_takes_the_quiet_update_path(monkeypatch):
    """Hosted Ken never runs the hourly pull-and-restart (it could cut someone off); it waits for a quiet spell."""
    import bot
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://app.example.dev/box/checkin")
    monkeypatch.setattr(bot, "FROZEN", False); monkeypatch.delenv("KEN_NO_AUTOUPDATE", raising=False)
    went = []

    async def quiet(app_dir):
        went.append(app_dir)
    monkeypatch.setattr(bot, "update_when_quiet", quiet)
    await bot.self_update()
    assert went == [bot.KEN_HOME / "app"]


async def test_hosted_ken_updates_in_a_quiet_spell_never_while_in_use(tmp_path, monkeypatch):
    """A hosted computer wakes exactly as it slept, so Ken restarts into a new engine itself, but only once nobody has
    used it for a while: never with a page open or a message coming in."""
    import subprocess
    import web
    run = lambda cwd, *a: subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()
    origin, app = tmp_path / "origin", tmp_path / "app"
    origin.mkdir()
    run(origin, "init", "-q", "-b", "main"); run(origin, "config", "user.email", "t@t"); run(origin, "config", "user.name", "t")
    (origin / "engine.py").write_text("1"); run(origin, "add", "."); run(origin, "commit", "-qm", "a")
    subprocess.run(["git", "clone", "-q", str(origin), str(app)], check=True)
    monkeypatch.setattr(bot, "RUNNING_REV", run(app, "rev-parse", "HEAD"))
    (origin / "engine.py").write_text("2"); run(origin, "add", "."); run(origin, "commit", "-qm", "b")

    class Restarted(BaseException):  # like os._exit, nothing after it runs
        pass

    def exit_(code):
        raise Restarted

    real_sleep = asyncio.sleep
    monkeypatch.setattr(bot, "QUIET_CHECK_EVERY", 0.01)
    monkeypatch.setattr(bot.os, "_exit", exit_)
    monkeypatch.setattr(bot, "restart_would_cut_in", lambda: False)

    page = object()
    web.OPEN_PAGES.add(page)  # someone has Ken open: no update, however long
    monkeypatch.setattr(web, "LAST_ASKED", time.monotonic() - 10_000)  # last asked long ago (not "since boot": CI boots just before)
    task = asyncio.ensure_future(bot.update_when_quiet(app))
    await real_sleep(0.3)
    assert not task.done()
    task.cancel()

    web.OPEN_PAGES.discard(page)  # they left, and it's been quiet: restart into the new version
    with pytest.raises(Restarted):
        await asyncio.wait_for(bot.update_when_quiet(app), 10)
