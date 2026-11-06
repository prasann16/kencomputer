"""Hosted Ken: a file too big for Telegram to pass on (over 20 MB) gets a one-tap link at once, the web app takes the
file instead, and Ken carries on with what was asked, answering in Telegram. The same for the shared bot and own bots
(both reach Ken as pushed updates)."""
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from engine import HOME  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401

SITE = "https://app.example.dev"
MB = 1024 * 1024


class Msg:
    def __init__(self, caption="make a reel of this"):
        self.chat_id, self.message_id, self.caption, self.replies = 777001, 42, caption, []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


@pytest.fixture
def waiting(monkeypatch):
    monkeypatch.setattr(bot, "BIG_FILES", Path(tempfile.mkdtemp()) / "telegram-big-files.json")


@pytest.fixture
def hosted(monkeypatch, waiting):
    monkeypatch.setenv("KEN_KEEPALIVE_URL", f"{SITE}/box/checkin")


async def test_too_big_replies_at_once_with_a_link_and_keeps_what_they_asked(hosted):
    msg = Msg()
    assert await bot.too_big(msg, SimpleNamespace(file_size=180 * MB), "a video", "IMG_0042.MOV")
    [reply] = msg.replies
    token = re.fullmatch(rf"That video is too big for Telegram to pass on\. Send it here: {SITE}/send/([0-9a-f]{{24}})", reply).group(1)
    assert bot.waiting_big_file(token) | {"ts": 0} == {"chat_id": 777001, "message_id": 42, "caption": "make a reel of this",
                                                       "kind": "a video", "name": "IMG_0042.MOV", "size": 180 * MB, "ts": 0}
    other = Msg("")
    assert await bot.too_big(other, SimpleNamespace(file_size=30 * MB), "a file", "notes.pdf")
    assert other.replies[0].startswith("That file is too big for Telegram to pass on.")
    assert await bot.too_big(other, SimpleNamespace(file_size=40 * MB), "a recording", "call.m4a")
    assert other.replies[1].startswith("That recording is too big")


async def test_small_files_go_through_and_ken_on_its_own_computer_says_what_it_always_did(waiting, monkeypatch):
    msg = Msg()
    assert not await bot.too_big(msg, SimpleNamespace(file_size=19 * MB), "a video", "a.mp4")
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)
    assert await bot.too_big(msg, SimpleNamespace(file_size=50 * MB), "a video", "a.mp4")
    assert msg.replies == ["That's 50 MB, and Telegram only lets me download files up to 20 MB. Send me a link to it "
                           "instead (iCloud, Google Drive or Dropbox), or drop it into the Ken app on your computer."]


async def test_links_expire_after_a_day(hosted, monkeypatch):
    msg = Msg()
    await bot.too_big(msg, SimpleNamespace(file_size=50 * MB), "a video", "a.mp4")
    token = msg.replies[0].rsplit("/", 1)[1]
    now = bot.time.time()
    monkeypatch.setattr(bot.time, "time", lambda: now + 86401)
    assert bot.waiting_big_file(token) is None and bot.take_big_file(token) is None
    assert bot.waiting_big_file("../../etc") is None


async def test_when_it_arrives_ken_carries_on_and_answers_in_telegram(hosted, monkeypatch, tmp_path):
    msg = Msg()
    await bot.too_big(msg, SimpleNamespace(file_size=180 * MB), "a video", "IMG_0042.MOV")
    token = msg.replies[0].rsplit("/", 1)[1]
    sent, asked = [], {}

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))

    async def send_message(pid, text, files=None, **kw):
        asked.update(pid=pid, text=text, files=files, **kw)
        await kw["listener"]("Your reel is ready.")
        return True

    monkeypatch.setattr(bot, "ENGINE", SimpleNamespace(_bg=[], send_message=send_message))
    monkeypatch.setattr(bot, "log_history", lambda *a: None)
    monkeypatch.setattr(bot, "flush_outbox", lambda *a, **k: asyncio.sleep(0))
    phone = bot.Phone()
    phone.app = SimpleNamespace(bot=FakeBot())
    assert phone.waiting_file(token) == {"kind": "a video", "name": "IMG_0042.MOV", "caption": "make a reel of this", "size": 180 * MB}
    path = tmp_path / "IMG_0042.MOV"
    assert await phone.file_arrived(token, path)
    await asyncio.gather(*bot.ENGINE._bg)
    assert asked["pid"] == HOME and asked["text"] == "make a reel of this" and asked["files"] == ["IMG_0042.MOV"]
    assert asked["via"] == "telegram" and f"saved at {path}" in asked["prompt"] and 'Their message with it: "make a reel of this"' in asked["prompt"]
    assert sent == [(777001, "Your reel is ready.")]
    assert not await phone.file_arrived(token, path)  # used once
    assert phone.waiting_file(token) is None


async def test_the_web_app_endpoints(engine, aiohttp_client, hosted):  # noqa: F811
    calls = []

    class FakePhone:
        def waiting_file(self, token):
            return {"kind": "a video", "name": "IMG_0042.MOV", "caption": "", "size": 1} if token == "a" * 24 else None

        async def file_arrived(self, token, path):
            calls.append((token, path))
            return token == "a" * 24

    app, client = await signed_in(engine, aiohttp_client, phone=FakePhone())
    assert (await (await client.get(f"/api/telegram-files/{'a' * 24}")).json())["name"] == "IMG_0042.MOV"
    gone = await client.get(f"/api/telegram-files/{'b' * 24}")
    assert gone.status == 404 and "expired" in (await gone.json())["error"]
    assert (await client.post(f"/api/telegram-files/{'a' * 24}", json={"file": "missing.mov"})).status == 400
    assert (await client.post(f"/api/telegram-files/{'a' * 24}", json={"file": "../secrets"})).status == 400
    engine.files_dir(HOME).mkdir(parents=True, exist_ok=True)
    (engine.files_dir(HOME) / "IMG_0042.MOV").write_bytes(b"video")
    r = await client.post(f"/api/telegram-files/{'a' * 24}", json={"file": "IMG_0042.MOV"})
    assert r.status == 200 and calls == [("a" * 24, engine.files_dir(HOME) / "IMG_0042.MOV")]
    assert (await client.post(f"/api/telegram-files/{'b' * 24}", json={"file": "IMG_0042.MOV"})).status == 404
