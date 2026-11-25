"""What they told the hosting site while their computer was being set up (hosted Ken's welcome): Ken's next turn is
told, and Ken files it where it belongs itself (only their name for Ken is written, so its chat shows it); the morning brief job and the getting-to-know
checklist are set, so Ken is useful from minute one and doesn't ask again."""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from engine import HOME  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401

REPO = Path(__file__).resolve().parent.parent
FACTS = {"call_name": "Sam", "work": "runs a two-person design studio", "busy": "a rebrand for a bakery, due Friday",
         "apps": ["Gmail", "Slack", "Notion"], "brief": "08:00", "ken_name": "Juno", "style": "short and direct, a bit of humour"}


def fresh_soul(engine):
    engine.context_file(HOME).parent.mkdir(parents=True, exist_ok=True)
    engine.context_file(HOME).write_text((REPO / "SOUL.template.md").read_text().replace("{{NAME}}", "my human"))


def test_only_the_name_is_written_for_ken_its_next_turn_is_told(engine):
    fresh_soul(engine)
    before = engine.context_file(HOME).read_text()
    assert engine.home_name() == "Ken" and engine.chats_list()[0]["name"] == "Ken"
    covered = engine.apply_onboarding(FACTS)
    assert set(covered) == {"ken-name", "call-you", "work", "tools", "brief-time", "style"}
    after = engine.context_file(HOME).read_text()
    assert after.splitlines()[0] == "# You are Juno" and after.splitlines()[1:] == before.splitlines()[1:]
    assert engine.home_name() == "Juno"  # the soul file has their name for Ken
    assert engine.chats_list()[0]["name"] == "Ken" and engine.chat_view(HOME)["name"] == "Ken"  # the chat stays titled Ken
    assert not (engine.memory_dir(HOME) / "about-them.md").exists()
    told = engine.kv_get(f"onboarding:said:{HOME}")
    for part in ("call them: Sam", "their work: runs a two-person design studio", "would like help with: a rebrand",
                 "how to talk to them: short and direct"):
        assert part in told


async def test_an_open_chat_picks_it_up_and_the_next_reply_is_told(engine):
    fresh_soul(engine)
    await engine.send_message(HOME, "hi")
    before = engine.chats[HOME]
    engine.apply_onboarding(FACTS)
    await engine.send_message(HOME, "who are you?")
    assert engine.chats[HOME] is not before  # reopened (resuming the same conversation), so it starts with the new soul
    assert engine.chats[HOME].session_id == "sess-1"
    ask = engine.brain.asks[-1]
    assert "they told the site" in ask["prompt"] and "call them: Sam" in ask["prompt"] and "your name: Juno" in ask["prompt"]
    assert "update your SOUL.md" in ask["prompt"]
    await engine.send_message(HOME, "thanks")
    assert "they told the site" not in engine.brain.asks[-1]["prompt"]  # once


def test_only_what_they_said_and_nothing_unfit(engine):
    fresh_soul(engine)
    assert engine.apply_onboarding({"busy": "the tax return; key is sk-ant-api03-abcdefghijklmnop", "brief": "no"}) == ["brief-time"]
    told = engine.kv_get(f"onboarding:said:{HOME}")
    assert "sk-ant" not in told and "morning brief: no" in told


async def test_the_api_applies_once_with_the_brief_job_and_the_checklist(engine, aiohttp_client, monkeypatch, tmp_path):  # noqa: F811
    fresh_soul(engine)
    jobs, setup = tmp_path / "jobs.json", tmp_path / "setup.json"
    shutil.copy(REPO / "jobs.default.json", jobs)
    setup.write_text(json.dumps({k: "unset" for k in bot.SETUP_ITEMS}))
    monkeypatch.setattr(bot, "JOBS_FILE", jobs)
    monkeypatch.setattr(bot, "SETUP_FILE", setup)
    monkeypatch.setattr(bot, "ENGINE", engine)
    app, client = await signed_in(engine, aiohttp_client, onboarding=bot.apply_onboarding)
    assert (await client.post("/api/onboarding", json={"facts": FACTS})).status == 400  # needs an id
    r = await (await client.post("/api/onboarding", json={"id": "onb-1", "facts": FACTS})).json()
    assert r["ok"] and "brief-time" in r["covered"]
    brief = next(j for j in json.loads(jobs.read_text()) if j["name"] == "morning-brief")
    assert brief["time"] == "08:00" and brief.get("on", True)
    assert {k: v for k, v in json.loads(setup.read_text()).items() if v == "done"}.keys() == {"ken-name", "call-you", "work", "tools", "brief-time"}
    again = await (await client.post("/api/onboarding", json={"id": "onb-1", "facts": FACTS})).json()
    assert again == {"ok": True, "already": True}
    await client.post("/api/onboarding", json={"id": "onb-2", "facts": {"brief": "no"}})
    assert next(j for j in json.loads(jobs.read_text()) if j["name"] == "morning-brief")["on"] is False


def test_the_chat_name_comes_from_the_soul_title_or_is_ken(engine):
    soul = engine.context_file(HOME)
    soul.parent.mkdir(parents=True, exist_ok=True)
    for title, name in (("# You are Ken (until Sam names you)", "Ken"), ("# You are Jarvis", "Jarvis"),
                        ("# You are Juno, Sam's assistant.", "Juno, Sam's assistant"), ("# Jarvis's soul", "Ken"), ("", "Ken")):
        soul.write_text(f"{title}\n\nYou help Sam.\n")
        assert engine.home_name() == name, title
    soul.unlink()
    assert engine.home_name() == "Ken"
