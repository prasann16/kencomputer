"""Ken on your phone: the first Start after connecting owns the bot; connect/disconnect from Settings."""
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
    for key in ("TELEGRAM_BOT_TOKEN", "ALLOWED_USER_ID", "TELEGRAM_OWNER"):
        monkeypatch.delenv(key, raising=False)
    p = bot.Phone()
    p.pair_until = time.time() + 60  # Settings just connected the bot
    return p


def test_the_first_start_makes_you_the_owner(phone):
    assert phone.pair(111, "Prasann")
    assert bot.ALLOWED_USER_ID == 111
    assert "ALLOWED_USER_ID=111" in (bot.KEN_HOME / ".env").read_text()
    assert os.environ["TELEGRAM_OWNER"] == "Prasann"
    assert json.loads(bot.SETUP_FILE.read_text())["phone"] == "done"
    # Nobody else can take it over afterwards.
    assert not phone.pair(222, "Someone")
    assert bot.ALLOWED_USER_ID == 111


def test_a_start_long_after_connecting_does_nothing(phone):
    phone.pair_until = time.time() - 1
    assert not phone.pair(111, "Stranger")
    assert bot.ALLOWED_USER_ID == 0


async def test_a_garbled_code_is_explained_without_calling_telegram(phone):
    with pytest.raises(ValueError, match="BotFather"):
        await phone.connect("not a token")


async def test_disconnect_forgets_the_bot_and_its_owner(phone, monkeypatch):
    bot.save_env(bot.KEN_HOME / ".env", "TELEGRAM_BOT_TOKEN", "123:abc")
    bot.save_env(bot.KEN_HOME / ".env", "ALLOWED_USER_ID", "111")
    monkeypatch.setattr(bot, "ALLOWED_USER_ID", 111)
    status = await phone.disconnect()
    assert status == {"connected": False, "bot": "", "paired": False, "owner": ""}
    env = (bot.KEN_HOME / ".env").read_text()
    assert "TELEGRAM_BOT_TOKEN" not in env and "ALLOWED_USER_ID" not in env
    assert bot.ALLOWED_USER_ID == 0
