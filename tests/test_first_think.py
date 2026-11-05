"""A brand-new Ken in the cloud no longer asks how it should think: it starts on Ken AI credits (the free taste, or
the plan's), noted once in setup.json "ai". Never on the Mac; not when their own Claude or ChatGPT is already in.
Ken speaks first right away, in the app and on Telegram."""
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


def test_a_new_ken_in_the_cloud_starts_on_credits_without_asking(tmp_path, credits, monkeypatch):
    setup = accounts(tmp_path)
    assert not setup.ask_ai() and setup.status()["ask_ai"] is False
    assert json.loads((tmp_path / "setup.json").read_text())["ai"] == "credits"
    with pytest.raises(ValueError):
        setup.choose_ai("gemini")
    # Not chosen for them: a Ken that has spoken already, their own account already in, or no credits on offer.
    for home, kw in ((tmp_path / "a", {"fresh": False}), (tmp_path / "b", {"codex": True})):
        assert not accounts(home, **kw).ask_ai() and not (home / "setup.json").exists()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "x" * 40)
    assert not accounts(tmp_path / "c").ask_ai() and not (tmp_path / "c" / "setup.json").exists()
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.delenv("KEN_INCLUDED_AI_TOKEN")
    assert not accounts(tmp_path / "d").ask_ai() and not (tmp_path / "d" / "setup.json").exists()


def test_never_on_the_mac(tmp_path, credits, monkeypatch):
    monkeypatch.delenv("KEN_PLATFORM")
    assert not accounts(tmp_path).ask_ai() and not (tmp_path / "setup.json").exists()


def test_connecting_their_own_account_on_that_screen_is_the_choice(tmp_path, credits, monkeypatch):
    setup = accounts(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "x" * 40)
    setup._chose("claude")
    assert setup.providers["claude"].ai_choice() == "claude"
    other = accounts(tmp_path / "x")
    other.providers["codex"].connected = True
    other._chose("codex")
    assert other.providers["claude"].ai_choice() == "codex"


async def test_ken_speaks_first_right_away_on_credits(engine, aiohttp_client, tmp_path, credits, monkeypatch):  # noqa: F811
    monkeypatch.setattr(engine, "new_install", lambda: True)
    monkeypatch.setattr(engine, "backend", lambda pid: "claude")
    setup = accounts(tmp_path)
    app, client = await signed_in(engine, aiohttp_client, setup=setup)
    assert (await (await client.get("/api/setup")).json())["ask_ai"] is False
    assert setup.providers["claude"].ai_choice() == "credits"
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


async def test_telegram_first_contact_goes_straight_to_ken(new_bot):
    bot, sent = new_bot
    update = telegram_update()
    with pytest.raises(SystemExit):
        await bot.handle_prompt(update, "hi Ken")
    assert update.effective_message.replies == [] and len(sent) == 1
    assert not bot.asking_ai() and bot.ACCOUNTS.providers["claude"].ai_choice() == "credits"
