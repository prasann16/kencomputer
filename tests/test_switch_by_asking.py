"""Switching AI by asking Ken (any surface): `--use credits|claude|codex`, the prompt line that says so, and a reply on
Telegram where a limit or credits-out message used to point to Settings."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
import onboarding  # noqa: E402
from brains import AIUnavailable  # noqa: E402
from engine import HOME  # noqa: E402
from onboarding import BrainOnboarding, Onboarding  # noqa: E402
from test_auto_credits import ken, left_on_credits, said  # noqa: E402,F401
from test_auto_credits import PlanSession as _PlanSession  # noqa: E402
from web import WebApp, use_ai  # noqa: E402


class Codex:
    active = False

    def __init__(self, connected=True):
        self.connected = connected

    def status(self):
        return {"connected": self.connected}

    def stop(self):
        pass


def accounts(e, codex=True):
    """Settings' accounts, where connecting or switching reopens free chats and marks busy ones (bot.brain_connected)."""
    async def reopen():
        for pid in list(e.chats):
            if e.chat_busy(pid):
                e.chat_tools.pop(pid, None)
            else:
                await e.chats.pop(pid).close()
    claude = Onboarding(e.home, lambda: True, reopen, lambda: True)
    return BrainOnboarding({"claude": claude, "codex": Codex(codex)}, lambda: e.backend(HOME))


@pytest.mark.parametrize("start", ["claude", "codex"])
async def test_asked_mid_reply_the_switch_lands_on_the_next_message(ken, monkeypatch, start):
    """The reply that runs `--use credits` finishes as it is; the next message opens a new session on Ken AI credits."""
    setup = accounts(ken)
    await ken.set_runtime(HOME, backend=start)
    answer = _PlanSession.ask

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        reply = await answer(self, prompt, on_text, on_tool, on_delta)
        if "switch me" in prompt:
            await use_ai(ken, setup, "credits")  # what `--use credits` reaches, while this reply holds the chat
        return reply

    monkeypatch.setattr(_PlanSession, "ask", ask)
    ok = await ken.send_message(HOME, "switch me to Ken credits")
    assert ok, said(ken)
    first = ken.chats[HOME]
    assert ken.backend(HOME) == "claude" and os.environ.get("KEN_USE_CREDITS") == "1"
    assert said(ken)[-1] == f"answer on {start}"  # finished on the AI it started on
    assert await ken.send_message(HOME, "hi")
    assert ken.chats[HOME] is not first and said(ken)[-1] == "answer on credits"
    assert ken.brains["claude"].asks[-1]["credits"]


async def test_use_says_plainly_when_their_own_ai_isnt_connected(ken, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    setup = accounts(ken, codex=False)
    with pytest.raises(ValueError, match="Your own Claude isn't connected"):
        await use_ai(ken, setup, "claude")
    with pytest.raises(ValueError, match="Your own ChatGPT isn't connected"):
        await use_ai(ken, setup, "codex")
    monkeypatch.delenv("KEN_INCLUDED_AI_TOKEN")
    with pytest.raises(ValueError, match="aren't part of this plan"):
        await use_ai(ken, setup, "credits")


async def test_the_use_command_switches_the_running_ken(ken, aiohttp_client):
    """`ken --use credits` asks the running Ken (its app server), the same switch as Settings → AI."""
    app = WebApp(ken, ken.home, setup=accounts(ken))
    client = await aiohttp_client(app.build())
    env = {**os.environ, "KEN_HOME": str(ken.home), "KEN_WEB_HOST": client.server.host, "KEN_WEB_PORT": str(client.server.port)}

    def use(ai):
        return subprocess.run([sys.executable, str(Path(bot.__file__)), "--use", ai], env=env, capture_output=True,
                              text=True, timeout=60)

    out = await asyncio.to_thread(use, "credits")
    assert out.returncode == 0 and out.stdout.startswith("Switched to Ken AI credits.")
    assert onboarding.included_ai_in_use() and os.environ.get("KEN_USE_CREDITS") == "1"
    out = await asyncio.to_thread(use, "claude")
    assert out.returncode == 0 and out.stdout.startswith("Switched to their own Claude.")
    assert not onboarding.included_ai_in_use()
    out = await asyncio.to_thread(use, "ChatGPT")
    assert out.returncode == 0 and ken.backend(HOME) == "codex"
    out = await asyncio.to_thread(use, "gemini")
    assert out.returncode == 2 and "credits | claude | codex" in out.stdout


async def test_the_use_command_passes_on_why_it_couldnt(ken, aiohttp_client, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    app = WebApp(ken, ken.home, setup=accounts(ken))
    client = await aiohttp_client(app.build())
    env = {**os.environ, "KEN_HOME": str(ken.home), "KEN_WEB_HOST": client.server.host, "KEN_WEB_PORT": str(client.server.port)}
    out = await asyncio.to_thread(subprocess.run, [sys.executable, str(Path(bot.__file__)), "--use", "claude"], env=env,
                                  capture_output=True, text=True, timeout=60)
    assert out.returncode == 1 and out.stdout.strip() == "Your own Claude isn't connected. Connect it in Settings → AI first."


FACTS = {"own": False, "app": True, "mac_app": False, "keep_awake": False, "browser": "", "telegram": True, "voice": False}


@pytest.mark.parametrize("kind,credits,told", [("cloud", True, True), ("server", True, True), ("cloud", False, False),
                                                ("mac", True, False)])
def test_the_prompt_says_ken_can_switch_only_hosted_with_credits(monkeypatch, kind, credits, told):
    monkeypatch.delenv("KEN_INCLUDED_AI_TOKEN", raising=False)
    if credits:
        monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://credits.example/v1")
        monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "included-key")
    facts = {**FACTS, "kind": kind, "own": kind == "mac", "app": kind != "server"}
    assert ("--use credits" in bot.where_you_are(facts)) == told


async def test_on_telegram_the_limit_and_credits_out_say_what_to_reply(ken):
    ken.plan.resets["claude"] = "22:20"
    limit = AIUnavailable(429, "rate_limit")
    assert ken.explain(limit, HOME, "telegram")[0] == (
        "You've reached your Claude plan's limit for now. It resets at 22:20. To keep going now, switch to Ken AI credits in "
        "Settings → AI on kencomputer.dev.")
    assert ken.explain(limit, HOME, "app")[0].endswith("switch to Ken AI credits in Settings → AI.")
    os.environ["KEN_USE_CREDITS"] = "1"  # they picked Ken AI credits
    onboarding.use_included_ai()
    ken.plan.credits_out = True
    assert not await ken.send_message(HOME, "hi", via="telegram")
    assert said(ken)[-1] == "You've used your Ken AI credits. Switch back to your own Claude in Settings → AI on kencomputer.dev (its limit resets at 22:20)."


async def test_settings_says_until_when_ken_switched_to_credits_by_itself(ken, aiohttp_client):
    """A switch an older Ken made at a plan's limit: /api/setup says until when (providers.claude.credits_until), only while it lasts."""
    app = WebApp(ken, ken.home, setup=accounts(ken))
    client = await aiohttp_client(app.build())
    await client.get(f"/login?token={app.token}", allow_redirects=False)

    async def until():
        return (await (await client.get("/api/setup")).json())["providers"]["claude"].get("credits_until")

    assert await until() is None
    ken.plan.limited["claude"] = True
    assert not await ken.send_message(HOME, "draft my post")  # at its limit: Ken doesn't switch by itself
    assert await until() is None
    left_on_credits(ken)  # a switch an older Ken made
    ken.plan.resets["claude"] = "22:20"
    assert await until() == "22:20"
    ken.plan.resets.clear()
    ken.plan.limited.clear()
    assert await ken.send_message(HOME, "thanks")  # back on their own Claude
    assert await until() is None
