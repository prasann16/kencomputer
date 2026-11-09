"""Voice mode (hosted): a fast talker speaks for Ken and hands real work to Ken. What it knows at the start of a call
(small, nothing unfit to say out loud), its two tools (ask Ken, remember), and the call landing in the chat."""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import HOME, speakable  # noqa: E402
import engine as engine_mod  # noqa: E402
from test_hosted import engine, hosted, signed_in  # noqa: E402,F401

WEB = Path(__file__).resolve().parent.parent / "web"


def soul(engine, text):
    engine.context_file(HOME).parent.mkdir(parents=True, exist_ok=True)
    engine.context_file(HOME).write_text(text)


def note(engine, name, text):
    engine.memory_dir(HOME).mkdir(parents=True, exist_ok=True)
    (engine.memory_dir(HOME) / name).write_text(text)


def test_the_context_pack_has_their_ken_their_life_today_and_where_the_chat_left_off(engine):
    soul(engine, "# You are Juno\n\nCalls them Sam. Short, direct answers; a bit of dry humour.\n")
    note(engine, "about-sam.md", "About Sam\nRuns Rise Bakery in Austin with Priya. Home by 6.")
    note(engine, "sourdough.md", "The new sourdough line: recipes and suppliers\n...")
    engine.log_message(HOME, "you", "can you draft the weekend specials post?")
    engine.log_message(HOME, "ken", "Done, it's in the chat for you to check.")
    engine.log_message(HOME, "ken", "☀️ Morning: dentist at 15:00, supplier call at 10:00.", job={"name": "morning-brief"})
    c = engine.voice_context()
    assert c["ken_name"] == "Juno"
    assert c["soul"].startswith("# You are Juno") and "dry humour" in c["soul"]
    assert "Runs Rise Bakery" in c["about_you"] and "Other things in memory: The new sourdough line" in c["about_you"]
    assert c["today"] == "☀️ Morning: dentist at 15:00, supplier call at 10:00."
    assert c["recent_chat"].splitlines()[:2] == ["Them: can you draft the weekend specials post?", "Juno: Done, it's in the chat for you to check."]
    assert c["now"] == engine_mod._day_and_time()
    assert set(c) == {"ken_name", "soul", "about_you", "today", "recent_chat", "now"}


def test_a_brief_from_yesterday_isnt_today(engine):
    engine.log_message(HOME, "ken", "old brief", job={"name": "morning-brief"})
    engine.db.execute("UPDATE events SET ts = ts - 86400 * 2")
    assert engine.voice_context()["today"] == ""
    assert engine.voice_context()["ken_name"] == "Ken"  # no soul file yet


def test_never_keys_tokens_or_paths_and_small_enough_to_stay_fast(engine):
    soul(engine, "# You are Ken\nANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop\nTheir notes live in /home/node/.ken/memory/x.md.\n" + "Likes tea. " * 1000)
    engine.log_message(HOME, "you", "my bot code is 1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw ok?")
    engine.log_message(HOME, "ken", "Saved it to ~/work/secrets.txt with ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    c = engine.voice_context()
    everything = " ".join(c.values())
    for secret in ("sk-ant", "AAHdqTc", "ghp_", "/home/node", "~/work", "API_KEY"):
        assert secret not in everything
    assert len(c["soul"]) <= 3002 and len(c["recent_chat"]) <= 1802
    assert sum(len(v) for v in c.values()) < 8000  # about 2k tokens at most
    assert speakable("see /Users/sam/Desktop/a.mov") == "see (a file)"


def test_remember_puts_what_they_said_into_kens_real_memory(engine):
    assert engine.remember("  Off work this Friday,\n Nov 7. ") == "Off work this Friday, Nov 7."
    engine.remember("Priya's birthday is Dec 2.")
    f = engine.memory_dir(HOME) / "voice.md"
    lines = f.read_text().splitlines()
    assert lines[0] == "Things they told me on voice calls (to file into the right notes)"
    assert lines[2] == f"- Off work this Friday, Nov 7. ({engine_mod._short_date()})"
    assert len(lines) == 4
    assert "Off work this Friday" in engine.voice_context()["about_you"]  # and the next call knows it


async def test_after_the_call_its_words_are_in_the_chat_and_ken_hears_them_once(engine):
    m = engine.voice_call_done([{"role": "you", "text": "what's on today?"}, {"role": "ken", "text": "[cheerful] Just the dentist at 3."},
                                {"role": "system", "text": "x"}, "junk"], 125.4)
    assert m["role"] == "ken" and m["call"] == {"seconds": 125} and m["via"] == "voice"
    assert m["text"] == "You: what's on today?\nKen: Just the dentist at 3."
    await engine.send_message(HOME, "and tomorrow?")
    assert "they talked with your voice side on a call: they: what's on today? | you: Just the dentist at 3." in engine.brain.asks[-1]["prompt"]
    await engine.send_message(HOME, "thanks")
    assert "voice side" not in engine.brain.asks[-1]["prompt"]
    assert engine.voice_call_done([], 30) is None
    assert "Just the dentist" not in engine.voice_context()["recent_chat"]  # the call log isn't repeated as chat


async def test_the_api_the_talker_uses(engine, aiohttp_client, hosted):  # noqa: F811
    soul(engine, "# You are Juno\n")
    app, client = await signed_in(engine, aiohttp_client)
    assert (await (await client.get("/api/voice/context")).json())["ken_name"] == "Juno"
    # A quick answer comes back to be spoken; the request is in the chat like a typed message.
    r = await (await client.post("/api/voice/ask", json={"request": "  what's   on today? "})).json()
    assert r == {"done": True, "answer": "did it"}
    you = [m for m in engine.messages(HOME) if m["role"] == "you"][-1]
    assert you["text"] == "what's on today?" and you["via"] == "voice"
    assert "said out loud on a voice call" in engine.brain.asks[-1]["prompt"]
    assert (await client.post("/api/voice/ask", json={"request": ""})).status == 400
    r = await client.post("/api/voice/remember", json={"fact": "Off Friday."})
    assert (await r.json())["saved"] == "Off Friday."
    assert (await client.post("/api/voice/remember", json={"fact": " "})).status == 400
    r = await (await client.post("/api/voice/done", json={"lines": [{"role": "you", "text": "bye"}], "seconds": 61})).json()
    assert r["message"]["call"] == {"seconds": 61}
    # Then Ken goes through the call, with the call in front of it.
    for _ in range(50):
        if "From our call" in engine.brain.asks[-1]["prompt"]:
            break
        await asyncio.sleep(0.02)
    assert "From our call" in engine.brain.asks[-1]["prompt"] and "You: bye" in engine.brain.asks[-1]["prompt"]
    # A call where they said nothing has nothing to go through.
    asks = len(engine.brain.asks)
    await client.post("/api/voice/done", json={"lines": [{"role": "ken", "text": "hello?"}], "seconds": 5})
    await asyncio.sleep(0.1)
    assert len(engine.brain.asks) == asks
    page = await client.get("/")
    assert "wss://api.elevenlabs.io" in page.headers["Content-Security-Policy"]


async def test_a_long_job_says_so_at_once_and_ken_finishes_in_the_chat(engine, aiohttp_client, monkeypatch):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    monkeypatch.setattr(type(app), "ASK_WAIT", 0.05)
    finished = asyncio.Event()

    async def slow(pid, text, files=None, **kw):
        engine.log_message(pid, "you", text, via=kw.get("via"))
        await kw["listener"]("Starting on the reel now.")
        await asyncio.sleep(0.3)
        engine.log_message(pid, "ken", "Your reel is ready.")
        finished.set()
        return True

    monkeypatch.setattr(engine, "send_message", slow)
    r = await (await client.post("/api/voice/ask", json={"request": "make a reel of yesterday's clips"})).json()
    assert r == {"done": False, "answer": "So far: Starting on the reel now. Ken is still working on it and will finish in the chat."}
    await asyncio.wait_for(finished.wait(), 2)
    assert engine.messages(HOME)[-1]["text"] == "Your reel is ready."


async def test_off_the_hosted_service_ken_doesnt_open_the_page_to_elevenlabs(engine, aiohttp_client, monkeypatch):  # noqa: F811
    monkeypatch.delenv("KEN_PLATFORM", raising=False)
    monkeypatch.delenv("KEN_HOSTED", raising=False)
    app, client = await signed_in(engine, aiohttp_client)
    assert "elevenlabs" not in (await client.get("/")).headers["Content-Security-Policy"]


def test_the_web_app_serves_the_voice_client_itself_and_shows_the_button_only_where_it_works():
    for f in ("client.js", "rawAudioProcessor.js", "audioConcatProcessor.js", "libsamplerate.worklet.js"):
        assert (WEB / "vendor" / "elevenlabs" / f).stat().st_size > 1000
    assert "var ElevenLabsClient=" in (WEB / "vendor" / "elevenlabs" / "client.js").read_text()[:200000]
    js = (WEB / "app.js").read_text()
    assert "'/vendor/elevenlabs/client.js'" in js and "'/vendor/elevenlabs/rawAudioProcessor.js'" in js
    assert "if (HOSTED && !window.kenDesktop) checkTalk();" in js
    assert "onTalk=${pid === 'home' && talk?.available ? () => setTalking(true) : null}" in js
    assert "cdn." not in js and "jsdelivr" not in js
