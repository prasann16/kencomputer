"""Ken on your phone: the first Start after connecting owns the bot; connect/disconnect from Settings."""
import asyncio
import json
import os
import tempfile
import time

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
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


async def test_saying_hi_pairs_a_just_connected_bot(phone, monkeypatch):
    monkeypatch.setattr(bot, "PHONE", phone)
    replies, prompts = [], []

    async def handle(update, text, context=""):
        prompts.append(text)

    monkeypatch.setattr(bot, "handle_prompt", handle)

    class Msg:
        text = "hi"
        message_id = 1
        reply_to_message = None
        async def reply_text(self, text, **kw):
            replies.append(text)

    def update(uid):
        return type("U", (), {"effective_user": type("User", (), {"id": uid, "first_name": "P"})(), "effective_message": Msg(),
                              "effective_chat": None})()

    bot.BORN_FLAG.touch()  # Ken already knows them: it says it's connected, then answers
    await bot.on_text(update(111), None)
    assert bot.ALLOWED_USER_ID == 111 and replies and "Connected" in replies[0] and prompts == ["hi"]
    await bot.on_text(update(222), None)  # the bot is theirs now: a stranger gets nothing
    assert prompts == ["hi"] and bot.ALLOWED_USER_ID == 111


async def test_ken_speaking_first_quotes_only_what_started_the_work(monkeypatch):
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append(kw.get("reply_parameters"))

    monkeypatch.setattr(bot, "ALLOWED_USER_ID", 111)
    await bot.send_outbound(FakeBot(), "☀️ brief")  # a job answers nobody
    await bot.send_outbound(FakeBot(), "x" * (bot.TELEGRAM_MAX + 10), reply_to=42)  # a run started by message 42
    assert sent[0] is None
    assert sent[1].message_id == 42 and sent[1].allow_sending_without_reply and sent[2] is None


def test_a_swipe_reply_tells_ken_what_it_points_at():
    def msg(earlier=None, quote=None):
        return type("M", (), {"reply_to_message": earlier, "quote": quote})()

    def earlier(text, is_bot=True):
        return type("E", (), {"text": text, "caption": None, "from_user": type("U", (), {"is_bot": is_bot})()})()

    assert bot.reply_context(msg()) == ""
    assert "your earlier message: \"Draft: shipped v2\"" in bot.reply_context(msg(earlier("Draft: shipped v2")))
    part = type("Q", (), {"text": "v2"})()
    assert '"v2"' in bot.reply_context(msg(earlier("Draft: shipped v2"), part))
    assert "their own" in bot.reply_context(msg(earlier("my note", is_bot=False)))


async def test_a_video_from_the_phone_reaches_ken_or_gets_a_reply(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import bot
    monkeypatch.setattr(bot, "authorized", lambda update: True)
    monkeypatch.setattr(bot, "INBOX_DIR", tmp_path)
    prompts, replies = [], []
    async def handle_prompt(update, prompt):
        prompts.append(prompt)
    monkeypatch.setattr(bot, "handle_prompt", handle_prompt)
    monkeypatch.setattr(bot, "log_history", lambda *a: None)
    class File:
        async def download_to_drive(self, path):
            open(path, "wb").write(b"mp4")
    def update(size):
        async def get_file():
            return File()
        async def reply_text(text):
            replies.append(text)
        async def send_action(action):
            pass
        video = SimpleNamespace(file_name="clip.mp4", file_size=size, get_file=get_file)
        msg = SimpleNamespace(video=video, video_note=None, animation=None, document=None, caption="add captions", reply_text=reply_text)
        return SimpleNamespace(effective_message=msg, effective_chat=SimpleNamespace(send_action=send_action))
    await bot.on_file(update(5 * 1024 * 1024), None)
    assert (tmp_path / "clip.mp4").read_bytes() == b"mp4" and "add captions" in prompts[0]
    await bot.on_file(update(180 * 1024 * 1024), None)  # over Telegram's 20 MB bot limit: say so, don't go quiet
    assert len(prompts) == 1 and "20 MB" in replies[0]
