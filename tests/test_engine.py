"""Engine + app server tests with a fake brain (no model calls)."""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brains import Brain, Session  # noqa: E402
from engine import GREET_AFTER, HOME, Engine, friendly_error  # noqa: E402


class FakeSession(Session):
    def __init__(self, brain, cwd, system, resume, model_fn=None, mcp_fn=None, env_fn=None):
        self.brain, self.cwd, self.system, self.session_id = brain, cwd, system, resume
        self.model_fn = model_fn or (lambda: "")
        self.env_fn = env_fn or (lambda: {})
        self.mcp_fn = mcp_fn or (lambda: {})
        self.busy = False
        self.warmed = False

    async def warm(self):
        self.warmed = True

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        self.brain.asks.append({"prompt": prompt, "system": self.system(), "cwd": self.cwd})
        self.busy = True
        try:
            if on_tool:
                await on_tool("Read · CONTEXT.md")
            if on_delta:
                await on_delta("did")
            await on_text("did it")
            self.session_id = self.session_id or "sess-1"
            return "did it"
        finally:
            self.busy = False

    async def interrupt(self):
        pass

    async def close(self):
        pass


class FakeBrain(Brain):
    def __init__(self):
        self.asks = []

    def session(self, *, cwd, system, resume=None, model_fn=None, mcp_fn=None, env_fn=None):
        return FakeSession(self, cwd, system, resume, model_fn, mcp_fn, env_fn)


async def test_backends_keep_distinct_sessions_and_agent_models(engine):
    agent = engine.create_agent("Writer")
    pid = agent["id"]
    engine.update_agent(pid, model="claude-test")
    engine.kv_set(f"session:{pid}", "claude-session")
    engine.brain.name = "codex"
    assert engine._chat_session(pid).session_id is None
    assert engine.get_agent(pid)["model"] == ""
    engine.update_agent(pid, model="gpt-test")
    await engine.send_message(pid, "hello")
    assert engine.kv_get(f"session:codex:{pid}") == "sess-1"
    assert engine.kv_get(f"session:{pid}") == "claude-session"
    engine.brain.name = "claude"
    assert engine.get_agent(pid)["model"] == "claude-test"
    engine.brain.name = "codex"
    assert engine.get_agent(pid)["model"] == "gpt-test"
    await engine.reset_chat(pid, clear=True)
    assert engine.kv_get(f"session:{pid}") is None
    assert engine.kv_get(f"session:codex:{pid}") is None


async def test_switching_from_legacy_claude_carries_recent_context_but_not_cleared_messages(engine):
    engine.log_message(HOME, "you", "old archived message")
    await engine.reset_chat(HOME, clear=True)
    engine.log_message(HOME, "you", "current context")
    engine.kv_set(f"session:{HOME}", "legacy-claude")
    engine.brain.name = "codex"
    await engine.send_message(HOME, "continue")
    prompt = engine.brain.asks[-1]["prompt"]
    assert "current context" in prompt and "old archived message" not in prompt


@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path, FakeBrain())
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    e.agents_dir.mkdir()
    e.requests_dir.mkdir()
    return e


async def settle(engine):
    for _ in range(50):
        await asyncio.sleep(0.02)
        if all(t.done() for t in engine.run_tasks.values()):
            return


def test_projects_are_folders(engine, tmp_path):
    a = engine.create_agent("My Reader", context="Read-it-later app.")
    b = engine.create_agent("My Reader")
    assert a["id"] == "my-reader" and b["id"] == "my-reader-2"
    assert "Read-it-later app." in (tmp_path / "agents/my-reader/CONTEXT.md").read_text()
    assert (tmp_path / "agents/my-reader/files").is_dir()
    assert [p["id"] for p in engine.list_agents()] == ["my-reader", "my-reader-2"]
    assert engine.create_agent("Home")["id"] == "home-agent"
    assert engine.get_autonomy("my-reader")["ask"]


def test_project_folder_must_exist(engine, tmp_path):
    with pytest.raises(ValueError):
        engine.create_agent("X", path=str(tmp_path / "nope"))
    repo = tmp_path / "repo"
    repo.mkdir()
    assert engine.create_agent("Repo", path=str(repo))["workdir"] == str(engine.agents_dir / "repo")


def test_bad_ids_and_paths_rejected(engine):
    for bad in ("../etc", "", "A", "a/b", HOME):
        with pytest.raises(KeyError):
            engine.get_agent(bad)
    engine.create_agent("P")
    p = engine.new_file_path("p", "../../evil.txt")
    assert p.parent == engine.files_dir("p") and p.name == "evil.txt"
    p.write_text("x")
    assert engine.new_file_path("p", "evil.txt").name == "evil-2.txt"
    with pytest.raises(KeyError):
        engine.file_path("p", "../project.json")
    assert engine.file_path("p", "evil.txt") == p


async def test_project_chat_is_ken_plus_context(engine, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    engine.create_agent("MyReader", path=str(repo), context="For heavy readers.")
    events = []
    q = engine.subscribe()
    await engine.send_message("myreader", "hi")
    while not q.empty():
        events.append(q.get_nowait()["type"])
    ask = engine.brain.asks[0]
    assert ask["cwd"] == str(engine.agents_dir / "myreader") and str(repo) in ask["system"]
    assert ask["system"].startswith("KEN AT WORK") and "For heavy readers." in ask["system"]
    assert ask["prompt"].startswith("[") and ask["prompt"].endswith("hi")
    assert [m["role"] for m in engine.messages("myreader")] == ["you", "ken"]
    assert {"chat.busy", "chat.tool", "chat.delta", "message"} <= set(events)
    assert engine.kv_get("session:myreader") == "sess-1"


async def test_home_chat_and_history_hook(engine):
    seen = []
    engine.history_hook = lambda role, text: seen.append((role, text))
    heard = []

    async def listener(t):
        heard.append(t)

    await engine.send_message(HOME, "plan my day", via="telegram", listener=listener)
    assert engine.brain.asks[0]["system"].startswith("KEN SOUL") and "Your tools and keys" in engine.brain.asks[0]["system"]
    assert heard == ["did it"]
    assert seen == [("you", "plan my day"), ("ken", "did it")]
    assert engine.messages(HOME)[0]["via"] == "telegram"


async def test_hidden_prompts_and_quiet(engine):
    await engine.send_message(HOME, "", prompt="(job fired)", log_user=False, quiet=True)
    assert engine.messages(HOME) == []
    assert engine.brain.asks[0]["prompt"].endswith("(job fired)")


async def test_open_speaks_first_only_after_a_while(engine):
    engine.create_agent("P")
    await engine.on_open("p")
    assert engine.chats["p"].warmed
    assert len(engine.brain.asks) == 1 and engine.messages("p")[-1]["role"] == "ken"
    await engine.on_open("p")
    assert len(engine.brain.asks) == 1  # just spoke; don't greet again
    engine.db.execute("UPDATE events SET ts = ?", (time.time() - GREET_AFTER - 5,))
    await engine.on_open("p")
    assert len(engine.brain.asks) == 2


async def test_dropped_files_are_the_instruction(engine):
    engine.create_agent("P")
    f = engine.new_file_path("p", "bank.csv")
    f.write_text("a,b")
    await engine.files_dropped("p", ["bank.csv"])
    you = engine.messages("p")[0]
    assert you["role"] == "you" and you["files"] == ["bank.csv"]
    prompt = engine.brain.asks[0]["prompt"]
    assert "bank.csv" in prompt


async def test_chat_list_and_reset(engine):
    engine.create_agent("P")
    await engine.send_message("p", "yo")
    chats = engine.chats_list()
    assert [c["id"] for c in chats] == [HOME, "p"]
    assert chats[1]["last"] == "did it"
    await engine.reset_chat("p")
    assert engine.kv_get("session:p") is None


async def test_web_auth_and_chats(engine, tmp_path, aiohttp_client):
    from web import WebApp

    app = WebApp(engine, tmp_path, rev="abc")
    client = await aiohttp_client(app.build())
    assert (await client.get("/api/chats")).status == 401
    assert (await client.get("/login?token=wrong", allow_redirects=False)).status == 403
    assert (await client.get(f"/login?token={app.token}", allow_redirects=False)).status == 302
    r = await client.post("/api/chats", json={"name": "MyReader"})
    assert r.status == 200
    chats = await (await client.get("/api/chats")).json()
    assert [c["id"] for c in chats] == [HOME, "myreader"]
    assert (await client.get("/api/chats/nope")).status == 404
    assert (await client.get("/api/chats/myreader/files/..%2Fproject.json")).status == 404
    assert (await client.get("/app.js")).status == 200
    assert (await client.get("/..%2Fbot.py")).status == 404


async def test_approve_starts_work_and_result_lands_in_chat(engine):
    engine.create_agent("MyReader")
    a = engine.create_approval("myreader", "post", "Offline post", "Offline mode is here", "post to X")
    await engine.resolve_approval(a["id"], "approve", "Offline mode is HERE")
    await settle(engine)
    assert engine.get_approval(a["id"])["status"] == "approved"
    run = engine.list_runs(project="myreader")[0]
    assert run["status"] == "done" and "Offline mode is HERE" in run["prompt"]
    last = engine.messages("myreader")[-1]
    assert last["role"] == "ken" and last["run_title"] == "Offline post"
    b = engine.create_approval("myreader", "spend", "Ads", "$50")
    await engine.resolve_approval(b["id"], "skip")
    assert engine.get_approval(b["id"])["status"] == "skipped"


async def test_agent_requests(engine):
    import json, os
    engine.create_agent("MyReader")
    (engine.requests_dir / "a.json").write_text(json.dumps({"type": "approval", "project": "MyReader", "kind": "email", "title": "Reply", "body": "Hi"}))
    (engine.requests_dir / "b.json").write_text(json.dumps({"type": "run", "project": "myreader", "title": "Spec", "prompt": "write it"}))
    (engine.requests_dir / "c.json").write_text("{nope")
    old = time.time() - 5
    for f in sorted(engine.requests_dir.glob("*.json")):
        os.utime(f, (old, old))
        engine._ingest(f)
    await settle(engine)
    assert engine.pending_approvals()[0]["kind"] == "email"
    assert engine.list_runs(project="myreader")[0]["title"] == "Spec"
    assert (engine.requests_dir / "rejected" / "c.json").exists()
    today = engine.today()
    assert today["approvals"] and today["done"] and today["agents"][0]["pending"] == 1


def test_project_model_rename_and_folder(engine, tmp_path):
    engine.create_agent("Reader")
    engine.update_agent("reader", model="claude-haiku-4-5", name="My Reader")
    p = engine.get_agent("reader")
    assert p["model"] == "claude-haiku-4-5" and p["name"] == "My Reader"
    session = engine._chat_session("reader")
    assert session.model_fn() == "claude-haiku-4-5"
    with pytest.raises(ValueError):
        engine.update_agent("reader", path=str(tmp_path / "nope"))
    with pytest.raises(ValueError):
        engine.update_agent("reader", name="  ")
    repo = tmp_path / "repo"
    repo.mkdir()
    p = engine.update_agent("reader", path=str(repo))
    assert p["workdir"] == str(engine.agents_dir / "reader")  # it still lives under Ken
    assert str(repo) in engine.system("reader") and engine._chat_session("reader").cwd == p["workdir"]


async def test_remove_project_keeps_its_folder(engine, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.txt").write_text("mine")
    engine.create_agent("Repo", path=str(repo))
    await engine.remove_agent("repo")
    assert [p["id"] for p in engine.list_agents()] == []
    assert (repo / "keep.txt").read_text() == "mine"
    assert any((tmp_path / "agents/.removed").iterdir())


async def test_ken_knows_where_a_message_came_from(engine):
    await engine.send_message(HOME, "hi", via="telegram")
    await engine.send_message(HOME, "hi again", via="app")
    await engine.send_message(HOME, "", prompt="(Scheduled job)")
    first, second, job = (a["prompt"].splitlines()[0] for a in engine.brain.asks)
    assert "on their phone (Telegram)" in first
    assert "in the Ken app" in second
    assert "phone" not in job and "app" not in job


def test_migrates_projects_to_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    old = tmp_path / "projects" / "reader"
    (old / "files").mkdir(parents=True)
    (old / "project.json").write_text('{"name": "Reader", "path": ""}')
    (old / "CONTEXT.md").write_text("# Reader")
    enc = lambda p: re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(p))  # noqa: E731
    history = tmp_path / "claude/projects" / enc(old)
    history.mkdir(parents=True)
    (history / "sess-1.jsonl").write_text("{}")
    e = Engine(tmp_path, FakeBrain())
    e.migrate()
    e.migrate()  # safe to repeat
    assert e.get_agent("reader")["name"] == "Reader"
    assert (tmp_path / "agents/reader/agent.json").exists() and (tmp_path / "agents/reader/memory").is_dir()
    assert (tmp_path / "projects").is_symlink() and (tmp_path / "projects/reader/CONTEXT.md").exists()
    assert (tmp_path / "claude/projects" / enc(tmp_path / "agents/reader") / "sess-1.jsonl").exists()


def test_migration_never_overwrites(tmp_path):
    for where, name in (("projects/a", "Old A"), ("projects/b", "B"), ("agents/a", "New A")):
        (tmp_path / where).mkdir(parents=True)
        (tmp_path / where / "agent.json").write_text(json.dumps({"name": name}))
    e = Engine(tmp_path, FakeBrain())
    e.migrate()
    assert e.get_agent("a")["name"] == "New A" and e.get_agent("b")["name"] == "B"
    assert (tmp_path / "projects/a").is_dir()  # the clash stays where it was


def test_agent_sees_only_its_folder_and_ken_sees_every_agent(engine):
    engine.create_agent("Reader", context="Grow MyReader revenue.")
    engine.create_agent("Brand", context="Grow my X following.")
    (engine.agents_dir / "brand/jobs.json").write_text('[{"name": "daily-post", "time": "09:00"}]')
    mine = engine.system("reader")
    assert "Grow MyReader revenue." in mine and "KEN AT WORK" in mine
    assert "Brand" not in mine and "X following" not in mine and "KEN SOUL" not in mine
    brief = engine.chief_brief()
    assert "Reader (reader): Grow MyReader revenue." in brief and "jobs: daily-post" in brief


async def test_each_agent_gets_its_own_tools(engine):
    engine.tools = lambda only, backend: {t: {} for t in sorted(only)}
    engine.create_agent("Reader")
    assert engine.tools_config("reader")["use"] == ["browser", "email", "calendar"]  # no tools.json: everything
    first = engine._chat_session("reader")
    stripe = {"type": "http", "url": "https://mcp.stripe.com"}
    (engine.agents_dir / "reader/tools.json").write_text(json.dumps({"use": ["email", "nope"], "mcp": {"stripe": stripe}}))
    assert engine.agent_tools("reader") == {"email": {}, "stripe": stripe}
    assert "stripe" in engine.system("reader")
    assert engine._chat_session("reader") is not first  # reopened with the new tools


async def test_ken_creates_an_agent_that_sets_itself_up(engine):
    (engine.requests_dir / "a.json").write_text(json.dumps({"type": "agent", "name": "Brand", "context": "Grow my X following."}))
    os.utime(engine.requests_dir / "a.json", (time.time() - 5,) * 2)  # finished writing
    engine._ingest(engine.requests_dir / "a.json")
    for _ in range(50):
        await asyncio.sleep(0.02)
        if engine.brain.asks:
            break
    assert engine.get_agent("brand")
    ask = engine.brain.asks[0]
    assert "You were just created" in ask["prompt"] and "Grow my X following." in ask["system"]


async def test_keys_reach_only_their_agent_and_never_the_screen(engine, tmp_path):
    engine.create_agent("Reader")
    engine.create_agent("Brand")
    first = engine._chat_session("reader")
    engine.set_key("reader", "stripe key", "rk_live_supersecret123")
    engine.set_key_file("reader", "firebase.json", b'{"private_key": "x"}')
    assert engine.list_keys("reader") == [{"name": "STRIPE_KEY", "kind": "value"}, {"name": "firebase.json", "kind": "file"}]
    store = tmp_path / "credentials/agents/reader"
    if os.name != "nt":  # Windows has no owner-only file modes; the user's own folder keeps them private there
        assert oct((store / "keys.json").stat().st_mode & 0o777) == "0o600" and oct(store.stat().st_mode & 0o777) == "0o700"
    session = engine._chat_session("reader")
    assert session is not first and session.env_fn() == {"STRIPE_KEY": "rk_live_supersecret123"}
    assert engine._chat_session("brand").env_fn() == {}
    prompt = engine.system("reader")
    assert "$STRIPE_KEY" in prompt and "firebase.json" in prompt and "supersecret" not in prompt
    assert "$STRIPE_KEY" not in engine.system("brand")
    assert engine.mask("reader", "key is rk_live_supersecret123.") == "key is ••••••."
    with pytest.raises(ValueError):
        engine.set_key("reader", "1BAD", "x")
    assert engine.remove_key("reader", "STRIPE_KEY") == [{"name": "firebase.json", "kind": "file"}]


async def test_ken_itself_gets_keys_tools_and_an_editable_about(engine):
    engine.tools = lambda only, backend: {t: {} for t in sorted(only)}
    engine.set_key(HOME, "OPENAI_KEY", "sk-proj-abcdef123456")
    s = engine._chat_session(HOME)
    assert s.env_fn() == {"OPENAI_KEY": "sk-proj-abcdef123456"} and set(s.mcp_fn()) == {"browser", "email", "calendar"}
    assert "$OPENAI_KEY" in engine.system(HOME) and "abcdef" not in engine.system(HOME)
    assert engine.mask(HOME, "sk-proj-abcdef123456") == "••••••"
    engine.set_tools_use(HOME, ["browser"])  # an old switch: Ken itself still uses everything connected
    assert set(engine._chat_session(HOME).mcp_fn()) == {"browser", "email", "calendar"}
    engine.set_about_context(HOME, "# You are Ken")
    engine.write_note(HOME, "people.md", "Who they work with")
    about = engine.about(HOME)
    assert about["context"].startswith("# You are Ken") and about["memory"] == [{"name": "people.md", "about": "Who they work with"}]
    with pytest.raises(KeyError):
        engine.write_note(HOME, "../escape.md", "x")


def test_share_an_agent_without_its_keys(engine):
    engine.create_agent("Brand", context="Grow my X following.")
    engine.set_key("brand", "X_TOKEN", "secret-token-123")
    (engine.agents_dir / "brand/tools.json").write_text(json.dumps({"use": ["browser"], "mcp": {"x": {"url": "u", "headers": {"Authorization": "secret-token-123"}}}}))
    engine.write_note("brand", "voice.md", "Short and dry.")
    data = engine.export_agent("brand")
    assert b"secret-token-123" not in data
    copy = engine.import_agent(data)
    assert copy["id"] == "brand-2" and "Grow my X following." in engine.get_context("brand-2")
    assert engine.read_note("brand-2", "voice.md").startswith("Short and dry.") and engine.list_keys("brand-2") == []



async def test_a_new_install_gets_kens_first_contact_once(engine):
    await engine.first_contact()
    assert engine.brain.asks == []  # not a new install: Ken waits for them
    engine.new_install = lambda: True
    await engine.first_contact()
    await engine.first_contact()  # it already spoke
    assert len(engine.brain.asks) == 1 and "very first contact" in engine.brain.asks[0]["prompt"]
    assert [m["role"] for m in engine.messages(HOME)] == ["ken"]


async def test_work_stops_only_after_the_brain_goes_quiet():
    import time as _time

    import engine as engine_mod

    class Session:
        last_activity = _time.monotonic()

    async def busy():  # keeps reporting progress, so it is never stopped
        for _ in range(6):
            Session.last_activity = _time.monotonic()
            await asyncio.sleep(0.05)
        return "done"
    assert await engine_mod.until_idle(Session, busy(), limit=0.1) == "done"

    async def silent():
        await asyncio.sleep(5)
    Session.last_activity = _time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await engine_mod.until_idle(Session, silent(), limit=0.1)


def test_failed_replies_read_as_plain_words_not_json():
    codex = Exception('{"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The \'gpt-6-astra\' model is not supported when using Codex with a ChatGPT account."}}')
    assert friendly_error(codex, "Codex") == "Codex can't use that model with your account. Pick another model at the top and send again."
    assert "expired" in friendly_error(Exception("401 Unauthorized"), "Claude")
    assert friendly_error(Exception("disk full"), "Claude") == "Something went wrong: disk full"


async def test_work_started_from_a_phone_message_quotes_it_when_done(engine):
    engine.create_agent("MyReader")
    q = engine.subscribe()
    engine.chat_origin = 42  # a turn answering phone message 42 asks for an OK
    a = engine.create_approval("myreader", "post", "Offline post", "Offline mode is here")
    engine.chat_origin = None
    await engine.resolve_approval(a["id"], "approve")
    await settle(engine)
    engine.start_run("myreader", "Later", "tidy up")  # nothing on the phone started this one
    await settle(engine)
    events = [q.get_nowait() for _ in range(q.qsize())]
    assert next(e for e in events if e["type"] == "approval.created")["origin"] == 42
    finished = [e for e in events if e["type"] == "run.finished"]
    assert [e["origin"] for e in finished] == [42, None]
    assert engine.origins == {}


async def test_a_turn_carries_its_phone_message_and_lets_go_after(engine):
    seen = []

    async def listener(text):
        seen.append(engine.chat_origin)

    await engine.send_message(HOME, "hi", via="telegram", listener=listener, origin=7)
    assert seen == [7] and engine.chat_origin is None


async def test_files_picked_in_the_mac_app_are_copied_by_path(engine, tmp_path, aiohttp_client):
    from web import WebApp
    app = WebApp(engine, tmp_path, rev="abc")
    client = await aiohttp_client(app.build())
    await client.get(f"/login?token={app.token}", allow_redirects=False)
    picked = tmp_path / "picked"
    picked.mkdir()
    (picked / "receipt.pdf").write_bytes(b"%PDF-1")
    r = await client.post("/api/chats/home/files/paths?attach=1", json={"paths": [str(picked / "receipt.pdf")]})
    assert r.status == 200 and (await r.json())["saved"] == ["receipt.pdf"]
    assert (engine.files_dir(HOME) / "receipt.pdf").read_bytes() == b"%PDF-1"
    assert (await client.post("/api/chats/home/files/paths", json={"paths": [str(picked)]})).status == 400  # a folder isn't a file
    assert (await client.post("/api/chats/home/files/paths", json={"paths": []})).status == 400


async def test_ken_hands_long_work_to_a_background_job_that_reports_back(engine):
    assert "BACKGROUND JOBS" in engine.system(HOME)
    f = engine.requests_dir / "job.json"
    f.write_text(json.dumps({"type": "run", "title": "🎬 Reel", "prompt": "edit /tmp/v.mp4 into a reel"}))
    old = time.time() - 5
    os.utime(f, (old, old))
    engine._ingest(f)
    await settle(engine)
    for _ in range(50):  # the report wakes Ken in the background
        await asyncio.sleep(0.02)
        if all(t.done() for t in engine._bg):
            break
    run = engine.list_runs(project=HOME)[0]
    assert run["status"] == "done" and run["source"] == "ken"
    job, report = engine.brain.asks[0], engine.brain.asks[1]
    assert job["prompt"] == "edit /tmp/v.mp4 into a reel"  # only the brief, never the conversation
    assert job["cwd"] == str(engine.home_cwd) and "You are a background job" in job["system"]
    assert 'background job "🎬 Reel" finished' in report["prompt"] and "did it" in report["prompt"]
    last = engine.messages(HOME)[-1]
    assert last["role"] == "ken" and last["via"] == "background"
    assert not [m for m in engine.messages(HOME) if m["role"] == "you"]


async def test_two_background_jobs_at_a_time_and_ken_can_stop_one(engine):
    gate = asyncio.Event()
    ask = FakeSession.ask

    async def slow(self, prompt, on_text, on_tool=None, on_delta=None):
        if prompt.startswith("job"):
            await gate.wait()
        return await ask(self, prompt, on_text, on_tool, on_delta)

    FakeSession.ask = slow
    try:
        runs = [engine.start_run(HOME, f"Job {i}", f"job {i}", source="ken") for i in range(3)]
        await asyncio.sleep(0.05)
        assert [engine._run_row(r["id"])["status"] for r in runs] == ["running", "running", "queued"]
        assert f'{runs[0]["id"]} "Job 0"' in await engine.state(HOME)
        engine.handle_request({"type": "stop", "run": runs[2]["id"]})
        await asyncio.sleep(0.05)
        assert engine._run_row(runs[2]["id"])["status"] == "stopped"
        gate.set()
        await settle(engine)
    finally:
        FakeSession.ask = ask
    assert [engine._run_row(r["id"])["status"] for r in runs] == ["done", "done", "stopped"]
