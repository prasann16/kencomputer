"""Engine + app server tests with a fake brain (no model calls)."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brains import Brain, Session  # noqa: E402
from engine import GREET_AFTER, HOME, Engine  # noqa: E402


class FakeSession(Session):
    def __init__(self, brain, cwd, system, resume):
        self.brain, self.cwd, self.system, self.session_id = brain, cwd, system, resume
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

    def session(self, *, cwd, system, resume=None):
        return FakeSession(self, cwd, system, resume)


@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path, FakeBrain())
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    e.projects_dir.mkdir()
    e.requests_dir.mkdir()
    return e


async def settle(engine):
    for _ in range(50):
        await asyncio.sleep(0.02)
        if all(t.done() for t in engine.run_tasks.values()):
            return


def test_projects_are_folders(engine, tmp_path):
    a = engine.create_project("My Reader", context="Read-it-later app.")
    b = engine.create_project("My Reader")
    assert a["id"] == "my-reader" and b["id"] == "my-reader-2"
    assert "Read-it-later app." in (tmp_path / "projects/my-reader/CONTEXT.md").read_text()
    assert (tmp_path / "projects/my-reader/files").is_dir()
    assert [p["id"] for p in engine.list_projects()] == ["my-reader", "my-reader-2"]
    assert engine.create_project("Home")["id"] == "home-project"
    assert engine.get_autonomy("my-reader")["ask"]


def test_project_folder_must_exist(engine, tmp_path):
    with pytest.raises(ValueError):
        engine.create_project("X", path=str(tmp_path / "nope"))
    repo = tmp_path / "repo"
    repo.mkdir()
    assert engine.create_project("Repo", path=str(repo))["workdir"] == str(repo)


def test_bad_ids_and_paths_rejected(engine):
    for bad in ("../etc", "", "A", "a/b", HOME):
        with pytest.raises(KeyError):
            engine.get_project(bad)
    engine.create_project("P")
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
    engine.create_project("MyReader", path=str(repo), context="For heavy readers.")
    events = []
    q = engine.subscribe()
    await engine.send_message("myreader", "hi")
    while not q.empty():
        events.append(q.get_nowait()["type"])
    ask = engine.brain.asks[0]
    assert ask["cwd"] == str(repo)
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
    assert engine.brain.asks[0]["system"] == "KEN SOUL"
    assert heard == ["did it"]
    assert seen == [("you", "plan my day"), ("ken", "did it")]
    assert engine.messages(HOME)[0]["via"] == "telegram"


async def test_hidden_prompts_and_quiet(engine):
    await engine.send_message(HOME, "", prompt="(job fired)", log_user=False, quiet=True)
    assert engine.messages(HOME) == []
    assert engine.brain.asks[0]["prompt"].endswith("(job fired)")


async def test_open_speaks_first_only_after_a_while(engine):
    engine.create_project("P")
    await engine.on_open("p")
    assert engine.chats["p"].warmed
    assert len(engine.brain.asks) == 1 and engine.messages("p")[-1]["role"] == "ken"
    await engine.on_open("p")
    assert len(engine.brain.asks) == 1  # just spoke; don't greet again
    engine.db.execute("UPDATE events SET ts = ?", (time.time() - GREET_AFTER - 5,))
    await engine.on_open("p")
    assert len(engine.brain.asks) == 2


async def test_dropped_files_are_the_instruction(engine):
    engine.create_project("P")
    f = engine.new_file_path("p", "bank.csv")
    f.write_text("a,b")
    await engine.files_dropped("p", ["bank.csv"])
    you = engine.messages("p")[0]
    assert you["role"] == "you" and you["files"] == ["bank.csv"]
    prompt = engine.brain.asks[0]["prompt"]
    assert "bank.csv" in prompt


async def test_chat_list_and_reset(engine):
    engine.create_project("P")
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
    engine.create_project("MyReader")
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
    engine.create_project("MyReader")
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
    assert today["approvals"] and today["done"] and today["projects"][0]["pending"] == 1
