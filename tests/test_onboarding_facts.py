"""What they told the hosting site while their computer was being set up (hosted Ken's voice onboarding, or its short
form) lands where Ken keeps such things: the soul file, memory, the morning brief job, and the getting-to-know
checklist, so Ken is useful from minute one and doesn't ask again."""
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
import engine as engine_mod  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401

REPO = Path(__file__).resolve().parent.parent
FACTS = {"call_name": "Sam", "work": "runs a two-person design studio", "busy": "a rebrand for a bakery, due Friday",
         "apps": ["Gmail", "Slack", "Notion"], "brief": "08:00", "ken_name": "Juno", "style": "short and direct, a bit of humour"}


def fresh_soul(engine):
    engine.context_file(HOME).parent.mkdir(parents=True, exist_ok=True)
    engine.context_file(HOME).write_text((REPO / "SOUL.template.md").read_text().replace("{{NAME}}", "my human"))


def test_soul_and_memory(engine):
    fresh_soul(engine)
    assert engine.voice_context()["ken_name"] == "Ken"  # not "Ken (until my human names you)"
    covered = engine.apply_onboarding(FACTS)
    assert set(covered) == {"ken-name", "call-you", "work", "tools", "brief-time", "style"}
    soul = engine.context_file(HOME).read_text()
    assert soul.startswith("# You are Juno\n") and "my human" not in soul
    assert "## About Sam\n- Call them Sam.\n- Work: runs a two-person design studio\n" in soul
    assert "(Nothing yet.)" not in soul.split("## Their rules")[0]
    assert "- How they like to be talked to: short and direct, a bit of humour" in soul.split("## Style")[1]
    note = (engine.memory_dir(HOME) / "about-them.md").read_text()
    assert note.startswith("About them: work, what's on their plate, the apps they use\n")
    assert f"From setting Ken up ({engine_mod._short_date()}):" in note
    for line in ("- Wants off their plate (first one first): a rebrand for a bakery, due Friday", "- Apps they live in: Gmail, Slack, Notion", "- Morning brief: 08:00"):
        assert line in note
    c = engine.voice_context()
    assert c["ken_name"] == "Juno" and "design studio" in c["about_you"]


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
    await engine.send_message(HOME, "thanks")
    assert "they told the site" not in engine.brain.asks[-1]["prompt"]  # once


def test_only_what_they_said_and_nothing_unfit(engine):
    fresh_soul(engine)
    before = engine.context_file(HOME).read_text()
    assert engine.apply_onboarding({"busy": "the tax return; key is sk-ant-api03-abcdefghijklmnop", "brief": "no"}) == ["brief-time"]
    assert engine.context_file(HOME).read_text().splitlines()[0] == before.splitlines()[0]
    note = (engine.memory_dir(HOME) / "about-them.md").read_text()
    assert "sk-ant" not in note and "- Morning brief: none" in note


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
    assert (engine.memory_dir(HOME) / "about-them.md").read_text().count("From setting Ken up") == 1
    await client.post("/api/onboarding", json={"id": "onb-2", "facts": {"brief": "no"}})
    assert next(j for j in json.loads(jobs.read_text()) if j["name"] == "morning-brief")["on"] is False
