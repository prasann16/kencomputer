"""Both accounts and per-chat runtimes coexist without restarting Ken."""
import asyncio

import pytest

from engine import Engine, HOME
from onboarding import BrainOnboarding
from test_engine import FakeBrain, FakeSession
from web import WebApp


@pytest.fixture
def engine(tmp_path):
    claude, codex = FakeBrain(), FakeBrain()
    claude.name, codex.name = "claude", "codex"
    claude.model_fn, codex.model_fn = lambda: "claude-default", lambda: "codex-default"
    e = Engine(tmp_path, claude, brains={"claude": claude, "codex": codex})
    e.configure_home(tmp_path / "work", lambda: "Ken")
    e.agents_dir.mkdir()
    e.requests_dir.mkdir()
    e.tools = lambda only, backend: {"runtime": backend}
    async def models(backend): return ["claude-test"] if backend == "claude" else ["gpt-test"]
    e.model_choices = models
    return e


async def test_both_runtimes_can_answer_concurrently(engine, monkeypatch):
    engine.create_agent("Coder", backend="codex")
    entered = {name: asyncio.Event() for name in engine.brains}
    release = asyncio.Event()
    original = FakeSession.ask
    async def ask(self, *args, **kwargs):
        entered[self.brain.name].set()
        await release.wait()
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(FakeSession, "ask", ask)
    tasks = [asyncio.create_task(engine.send_message(pid, "hello")) for pid in (HOME, "coder")]
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), 2)
        assert set(engine.chats[HOME].mcp_fn()) == {"tasks"}  # main Ken on Claude supervises tasks
        assert engine.chats["coder"].mcp_fn() == {"runtime": "codex"}
        with pytest.raises(ValueError, match="finish"):
            await engine.set_runtime(HOME, backend="codex")
        with pytest.raises(ValueError, match="finish"):
            await engine.set_runtime("coder", model="gpt-test")
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert len(engine.brains["claude"].asks) == len(engine.brains["codex"].asks) == 1
    assert "Runtime for this conversation: Codex" in engine.brains["codex"].asks[0]["system"]
    assert engine.kv_get("session:home") and engine.kv_get("session:codex:coder")


async def test_switch_and_restart_keep_each_runtime_session_and_model(engine, monkeypatch):
    closed = []
    async def close(self): closed.append(self.brain.name)
    monkeypatch.setattr(FakeSession, "close", close)
    await engine.set_runtime(HOME, model="claude-test")
    engine._chat_session(HOME).session_id = "claude-thread"
    await engine.send_message(HOME, "Remember the blue bird")
    await engine.set_runtime(HOME, backend="codex", model="gpt-test")
    assert closed == ["claude"]
    engine._chat_session(HOME).session_id = "codex-thread"
    await engine.send_message(HOME, "continue")
    assert "blue bird" in engine.brains["codex"].asks[-1]["prompt"]
    await engine.set_runtime(HOME, backend="claude")
    assert engine.model_for(HOME) == "claude-test"
    assert engine._chat_session(HOME).session_id == "claude-thread"
    await engine.set_runtime(HOME, backend="codex")
    restarted = Engine(engine.home, engine.brain, brains=engine.brains)
    assert restarted.backend(HOME) == "codex"
    assert restarted.model_for(HOME) == "gpt-test"
    assert restarted._chat_session(HOME).session_id == "codex-thread"
    await restarted.reset_chat(HOME, clear=True)
    assert restarted.kv_get("session:home") is None
    assert restarted.kv_get("session:codex:home") is None
    assert restarted.backend(HOME) == "codex"


async def test_agent_choice_and_background_jobs_are_independent_of_home(engine):
    engine.create_agent("Reader", backend="codex")
    await engine.set_runtime("reader", model="gpt-test")
    run = engine.start_run("reader", "Read", "read it")
    with pytest.raises(ValueError, match="finish"):
        await engine.set_runtime("reader", backend="claude")
    await engine.run_tasks[run["id"]]
    assert len(engine.brains["codex"].asks) == 1 and not engine.brains["claude"].asks
    await engine.set_runtime("reader", backend="claude", model="claude-test")
    await engine.set_runtime("reader", backend="codex")
    assert engine.model_for("reader") == "gpt-test"
    assert engine.backend(HOME) == "claude"
    await engine.set_runtime(HOME, backend="codex")
    engine.create_agent("New")
    assert engine.backend("new") == "codex"


async def test_runtime_api_persists_choices_and_rejects_invalid_requests(engine, aiohttp_client):
    app = WebApp(engine, engine.home)
    client = await aiohttp_client(app.build())
    url = "/api/chats/home/runtime"
    assert (await client.get(url)).status == 401
    assert (await client.get("/api/models")).status == 401
    await client.get(f"/login?token={app.token}", allow_redirects=False)
    r = await client.put(url, json={"backend": "codex", "model": "gpt-test"})
    assert r.status == 200
    assert await r.json() == {"backend": "codex", "current": "gpt-test", "choices": ["gpt-test"], "providers": ["claude", "codex"]}
    r = await client.get("/api/models")
    assert r.status == 200
    assert await r.json() == {"claude": ["claude-test"], "codex": ["gpt-test"]}
    assert engine.backend(HOME) == "codex" and engine.model_for(HOME) == "gpt-test"
    for bad in ({"backend": "unknown"}, {"backend": []}, {"model": "claude-test"}, {"model": []}):
        assert (await client.put(url, json=bad)).status == 400
    assert engine.backend(HOME) == "codex" and engine.model_for(HOME) == "gpt-test"
    assert (await client.put(url, json={"model": ""})).status == 200
    assert engine.model_for(HOME) == "codex-default"
    assert (await client.put("/api/chats/missing/runtime", json={"backend": "claude"})).status == 404
    r = await client.post("/api/chats", json={"name": "Writer", "backend": "claude"})
    assert (await r.json())["backend"] == "claude"


class Account:
    active = False
    def __init__(self, backend, connected=False):
        self.backend, self.connected, self.starts = backend, connected, 0
    def status(self): return {"backend": self.backend, "connected": self.connected}
    def voice_status(self): return {"state": "ready"}
    def phone_step(self): return "declined"
    def stop(self): pass
    async def start(self):
        self.starts += 1
        return {"url": "https://example.com/login"}
    async def submit(self, code): self.connected = True


async def test_connecting_second_account_preserves_first_and_chat_selection(engine, aiohttp_client):
    claude, codex = Account("claude", True), Account("codex")
    setup = BrainOnboarding({"claude": claude, "codex": codex}, lambda: engine.backend(HOME))
    app = WebApp(engine, engine.home, setup=setup)
    client = await aiohttp_client(app.build())
    await client.get(f"/login?token={app.token}", allow_redirects=False)
    r = await client.post("/api/setup", json={"action": "start", "backend": "codex"})
    status = await r.json()
    assert status["providers"]["claude"]["connected"] and not status["providers"]["codex"]["connected"]
    assert codex.starts == 1 and claude.starts == 0
    await client.post("/api/setup", json={"code": "done", "backend": "codex"})
    assert all(p["connected"] for p in setup.status()["providers"].values())
    assert engine.backend(HOME) == "claude"
    await engine.set_runtime(HOME, backend="codex")
    codex.connected = False
    assert (await client.post("/api/chats/home/messages", json={"text": "hello"})).status == 400
    assert (await client.post("/api/setup", json={"action": "start", "backend": "bogus"})).status == 400


async def test_claude_model_discovery_uses_runtime_and_deduplicates_aliases(monkeypatch):
    import claude_agent_sdk
    from brains import claude_models

    class Client:
        def __init__(self, options):
            assert options.setting_sources == [] and options.strict_mcp_config
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get_server_info(self):
            return {"models": [{"value": "default", "resolvedModel": "claude-test"},
                               {"value": "opus", "resolvedModel": "claude-test"},
                               {"value": "claude-other"}]}
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)
    assert await claude_models() == ["claude-test", "claude-other"]
