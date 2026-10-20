"""Ken on your phone: the first Start after connecting owns the bot; connect/disconnect from Settings."""
import asyncio
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
    assert status == {"connected": False, "bot": "", "paired": False, "owner": "", "handoff": None}
    env = (bot.KEN_HOME / ".env").read_text()
    assert "TELEGRAM_BOT_TOKEN" not in env and "ALLOWED_USER_ID" not in env
    assert bot.ALLOWED_USER_ID == 0



async def test_the_code_sent_from_the_phone_waits_for_their_ok(aiohttp_server, monkeypatch):
    import base64
    import os as _os

    from aiohttp import web
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    phone = bot.Phone()
    sealed = {}

    async def handoff(request):  # the site: hands the scrambled code out once
        if request.query.get("id") == phone.handoff.id and sealed:
            return web.json_response(sealed.pop("v"))
        return web.json_response({}, status=404)

    app = web.Application()
    app.router.add_get("/api/handoff", handoff)
    site = await aiohttp_server(app)
    monkeypatch.setattr(bot, "KEN_SITE", str(site.make_url("")).rstrip("/"))

    class FakeBot:
        def __init__(self, token):
            self.token = token
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get_me(self):
            return type("Me", (), {"username": "my_ken_bot"})()

    monkeypatch.setattr(bot, "Bot", FakeBot)
    connected = []

    async def connect(token):
        connected.append(token)
        return {"connected": True}

    monkeypatch.setattr(phone, "connect", connect)
    view = await phone.handoff.start()
    assert "<svg" in view["qr"] and phone.status()["handoff"] == view and view["error"] == ""
    token = "1234567890:AAH" + "z" * 33
    iv = _os.urandom(12)  # what the phone page sends: encrypted with the key from the QR
    ct = AESGCM(phone.handoff.key).encrypt(iv, token.encode(), phone.handoff.id.encode())
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    sealed["v"] = {"iv": b64(iv), "ct": b64(ct)}
    for _ in range(50):
        await asyncio.sleep(0.1)
        if phone.handoff.pending:
            break
    assert phone.status()["handoff"] == {"bot": "my_ken_bot"} and connected == []  # nothing until they say yes
    await phone.handoff.confirm()
    assert connected == [token] and phone.status()["handoff"] is None
