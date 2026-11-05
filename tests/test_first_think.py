"""A brand-new Ken in the cloud asks first how it should think: their Claude, their ChatGPT, or Ken AI credits.
Asked once (setup.json "ai"), never on the Mac, and nothing runs on the credits until they choose: not Ken's first
words in the app, not Telegram, not the scheduled jobs."""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import HOME  # noqa: E402
from onboarding import BrainOnboarding, Onboarding  # noqa: E402
from test_hosted import clean_ai, engine, hosted, signed_in  # noqa: E402,F401


async def _noop():
    return None


class Codex:
    active = False

    def __init__(self, connected=False):
        self.connected = connected

    def status(self):
        return {"backend": "codex", "connected": self.connected}

    def stop(self):
        pass


@pytest.fixture
def credits(monkeypatch, hosted, clean_ai):  # noqa: F811 - a Ken in the cloud with Ken AI credits on offer
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://site.example/ai")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "credits-tok")


def accounts(home, fresh=True, codex=False):
    claude = Onboarding(home, lambda: True, _noop, lambda: True)  # "connected": the credits
    return BrainOnboarding({"claude": claude, "codex": Codex(codex)}, lambda: "claude", fresh=lambda: fresh)


def test_asked_once_on_a_brand_new_ken_in_the_cloud_only(tmp_path, credits, monkeypatch):
    setup = accounts(tmp_path)
    assert setup.ask_ai() and setup.status()["ask_ai"] is True
    setup.choose_ai("credits")
    assert not setup.ask_ai()
    assert json.loads((tmp_path / "setup.json").read_text())["ai"] == "credits"
    with pytest.raises(ValueError):
        setup.choose_ai("gemini")
    # Not asked: a Ken that has spoken already, their own account already in, or no credits on offer.
    assert not accounts(tmp_path / "a", fresh=False).ask_ai()
    assert not accounts(tmp_path / "b", codex=True).ask_ai()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "x" * 40)
    assert not accounts(tmp_path / "c").ask_ai()
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.delenv("KEN_INCLUDED_AI_TOKEN")
    assert not accounts(tmp_path / "d").ask_ai()


def test_never_on_the_mac(tmp_path, credits, monkeypatch):
    monkeypatch.delenv("KEN_PLATFORM")
    assert not accounts(tmp_path).ask_ai()


def test_connecting_their_own_account_on_that_screen_is_the_choice(tmp_path, credits, monkeypatch):
    setup = accounts(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "x" * 40)
    setup._chose("claude")
    assert setup.providers["claude"].ai_choice() == "claude"
    other = accounts(tmp_path / "x")
    other.providers["codex"].connected = True
    other._chose("codex")
    assert other.providers["claude"].ai_choice() == "codex"


async def test_the_app_waits_for_the_choice_then_ken_speaks_first(engine, aiohttp_client, tmp_path, credits, monkeypatch):  # noqa: F811
    greeted = []

    async def first_contact():
        greeted.append(True)

    monkeypatch.setattr(engine, "first_contact", first_contact)
    monkeypatch.setattr(engine, "new_install", lambda: True)
    monkeypatch.setattr(engine, "backend", lambda pid: "claude")
    setup = accounts(tmp_path)
    app, client = await signed_in(engine, aiohttp_client, setup=setup)
    assert (await (await client.get("/api/setup")).json())["ask_ai"] is True
    view = await (await client.get(f"/api/chats/{HOME}")).json()
    assert not greeted and not view.get("busy")  # no first words, no credits spent
    await client.post(f"/api/chats/{HOME}/open")
    await client.post(f"/api/chats/{HOME}/warm")
    assert (await client.post("/api/setup", json={"action": "ai", "ai": "nope"})).status == 400
    r = await (await client.post("/api/setup", json={"action": "ai", "ai": "credits"})).json()
    assert r["ask_ai"] is False and setup.providers["claude"].ai_choice() == "credits"
    view = await (await client.get(f"/api/chats/{HOME}")).json()
    assert view["busy"] is True  # Ken's first words are on the way


async def test_trying_it_free_puts_home_on_claude(engine, aiohttp_client, tmp_path, credits, monkeypatch):  # noqa: F811
    switched = []

    async def set_runtime(pid, backend=None, model=None):
        switched.append((pid, backend))

    monkeypatch.setattr(engine, "backend", lambda pid: "codex")
    monkeypatch.setattr(engine, "set_runtime", set_runtime)
    app, client = await signed_in(engine, aiohttp_client, setup=accounts(tmp_path))
    await client.post("/api/setup", json={"action": "ai", "ai": "credits"})
    assert switched == [(HOME, "claude")]


# ------------------------------------------------------------------ Telegram

class Message:
    message_id = 7

    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)

    async def set_reaction(self, *a):
        pass


def telegram_update():
    async def send_action(*a):
        pass

    return SimpleNamespace(effective_message=Message(), effective_chat=SimpleNamespace(id=1, send_action=send_action))


@pytest.fixture
def new_bot(tmp_path, credits, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "ACCOUNTS", accounts(tmp_path))
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://site.example/box/checkin")
    sent = []

    async def send_message(pid, text, *a, **kw):
        sent.append(kw.get("prompt"))
        raise SystemExit("stop here")  # the rest of the turn isn't what's tested

    monkeypatch.setattr(bot.ENGINE, "send_message", send_message)
    monkeypatch.setattr(bot.ENGINE, "chat_busy", lambda pid: False)
    monkeypatch.setattr(bot.ENGINE, "backend", lambda pid: "claude")
    return bot, sent


async def test_telegram_first_contact_asks_in_one_line(new_bot):
    bot, sent = new_bot
    for words in ("hi Ken", bot.first_contact_prompt("pressed Start on their phone")):
        update = telegram_update()
        await bot.handle_prompt(update, words)
        assert update.effective_message.replies == [
            "Before I start: how should I think? Connect your Claude or ChatGPT subscription in Settings "
            "(https://site.example/ken#/settings/claude), or reply \"free\" to try me free on Ken AI credits."]
    assert sent == [] and bot.ACCOUNTS.ask_ai()
    assert bot.asking_ai()


async def test_telegram_free_starts_ken_on_credits(new_bot):
    bot, sent = new_bot
    update = telegram_update()
    with pytest.raises(SystemExit):
        await bot.handle_prompt(update, "Free!")
    assert bot.ACCOUNTS.providers["claude"].ai_choice() == "credits" and not bot.asking_ai()
    assert sent == [bot.first_contact_prompt("chose to try Ken free, on their phone")]
    assert update.effective_message.replies == []
    for words in ("try it free", "try me free", "FREE"):
        assert bot.FREE_REPLY.fullmatch(words)
    assert not bot.FREE_REPLY.fullmatch("free up my calendar")
