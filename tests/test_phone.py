"""Ken on your phone: pairing by one-time link, and connect/disconnect from Settings."""
import json
import os
import tempfile
import time

import pytest

os.environ.setdefault("KEN_HOME", tempfile.mkdtemp())
import bot  # noqa: E402


@pytest.fixture
def phone(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_ID", 0)
    for key in ("TELEGRAM_BOT_TOKEN", "ALLOWED_USER_ID"):
        monkeypatch.delenv(key, raising=False)
    p = bot.Phone()
    p.code, p.code_until = "one-time", time.time() + 60
    return p


def test_the_one_time_link_makes_you_the_owner(phone):
    assert not phone.pair(111, "wrong")
    assert not phone.pair(111, "")
    assert phone.pair(111, "one-time")
    assert bot.ALLOWED_USER_ID == 111
    assert "ALLOWED_USER_ID=111" in (bot.KEN_HOME / ".env").read_text()
    assert json.loads(bot.SETUP_FILE.read_text())["phone"] == "done"
    # Nobody else can take it over, even with the same link.
    assert not phone.pair(222, "one-time")
    assert bot.ALLOWED_USER_ID == 111


def test_an_expired_link_does_nothing(phone):
    phone.code_until = time.time() - 1
    assert not phone.pair(111, "one-time")
    assert bot.ALLOWED_USER_ID == 0


async def test_a_garbled_code_is_explained_without_calling_telegram(phone):
    with pytest.raises(ValueError, match="BotFather"):
        await phone.connect("not a token")


async def test_disconnect_forgets_the_bot_and_its_owner(phone, monkeypatch):
    bot.save_env(bot.KEN_HOME / ".env", "TELEGRAM_BOT_TOKEN", "123:abc")
    bot.save_env(bot.KEN_HOME / ".env", "ALLOWED_USER_ID", "111")
    monkeypatch.setattr(bot, "ALLOWED_USER_ID", 111)
    status = await phone.disconnect()
    assert status == {"connected": False, "bot": "", "paired": False}
    env = (bot.KEN_HOME / ".env").read_text()
    assert "TELEGRAM_BOT_TOKEN" not in env and "ALLOWED_USER_ID" not in env
    assert bot.ALLOWED_USER_ID == 0
