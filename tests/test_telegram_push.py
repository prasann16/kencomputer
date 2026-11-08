"""Hosted Ken in Telegram: its host pushes Telegram updates to Ken (a sleeping computer can't fetch them), and Ken's
own Telegram code runs against the host's stand-in for Telegram's API (TELEGRAM_API_URL)."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401

UPDATE = {"update_id": 5, "message": {"message_id": 9, "date": 1, "text": "what's on today?",
                                      "from": {"id": 777001, "is_bot": False, "first_name": "Sam"},
                                      "chat": {"id": 777001, "type": "private"}}}


@pytest.fixture
def phone(monkeypatch):
    from telegram.ext import Application

    monkeypatch.setattr(bot, "ALLOWED_USER_ID", 0)
    monkeypatch.setattr(bot, "KEN_HOME", Path(tempfile.mkdtemp()))
    p = bot.Phone()
    p.app = Application.builder().token("box-checkin-token").base_url("https://host.example/tg/bot").build()
    return p


async def test_a_pushed_update_reaches_ken_and_makes_its_sender_the_owner(phone):
    assert await phone.receive(UPDATE)
    update = phone.app.update_queue.get_nowait()
    assert update.effective_message.text == "what's on today?"
    assert bot.ALLOWED_USER_ID == 777001 and os.environ["TELEGRAM_OWNER"] == "Sam"
    assert "ALLOWED_USER_ID=777001" in (bot.KEN_HOME / ".env").read_text()


async def test_relinked_to_another_telegram_follows_the_host(phone):
    await phone.receive(UPDATE)
    other = {**UPDATE, "message": {**UPDATE["message"], "from": {"id": 5, "is_bot": False, "first_name": "Al"}, "chat": {"id": 5, "type": "private"}}}
    await phone.receive(other)
    assert bot.ALLOWED_USER_ID == 5


async def test_disconnected_on_the_site_stops_ken_messaging_there(phone):
    await phone.receive(UPDATE)
    assert await phone.receive({"ken_relay": "unlinked"})
    assert bot.ALLOWED_USER_ID == 0 and phone.app.update_queue.qsize() == 1


async def test_not_ready_yet_says_so_so_the_host_tries_again(phone):
    phone.app = None
    assert not await phone.receive(UPDATE)


async def test_the_push_endpoint(engine, aiohttp_client, monkeypatch, phone):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client, phone=phone)
    assert (await client.post("/api/telegram/update", json=UPDATE)).status == 404  # only when the host pushes
    monkeypatch.setenv("KEN_TELEGRAM_PUSH", "1")
    assert (await client.post("/api/telegram/update", json=["nope"])).status == 400
    r = await client.post("/api/telegram/update", json=UPDATE)
    assert r.status == 200 and phone.app.update_queue.qsize() == 1
    phone.app = None
    assert (await client.post("/api/telegram/update", json=UPDATE)).status == 503


async def test_push_mode_never_polls(monkeypatch):
    """A computer that sleeps can't fetch updates: in push mode Ken starts Telegram without polling."""
    calls = []

    class FakeApp:
        bot = type("B", (), {"username": "kenbot", "set_my_commands": staticmethod(lambda *a, **k: _noop())})()
        updater = type("U", (), {"start_polling": staticmethod(lambda **k: calls.append("poll") or _noop())})()

        def add_handler(self, *a, **kw):
            pass

        async def initialize(self):
            calls.append("init")

        async def start(self):
            calls.append("start")

    class Builder:
        def __getattr__(self, name):
            return lambda *a, **k: self if name != "build" else FakeApp()

    monkeypatch.setattr(bot.Application, "builder", staticmethod(lambda: Builder()))
    monkeypatch.setattr(bot, "notify_telegram", lambda app: _noop())
    monkeypatch.setattr(bot, "PUSHED", True)
    p = bot.Phone()
    await p.start("box-checkin-token")
    assert calls == ["init", "start"] and p.username == "kenbot"
    p.notify.cancel()


async def _noop():
    return None
