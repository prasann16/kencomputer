"""Codex session contract: streaming, resuming, interruption and tool transport."""
import asyncio
from types import SimpleNamespace

import pytest

import codex_brain
from codex_brain import CodexBrain, mcp_config


def event(method, **payload):
    return SimpleNamespace(method=method, payload=SimpleNamespace(model_dump=lambda **kwargs: payload))


class Client:
    def __init__(self, events):
        self.events, self.starts, self.resumes, self.prompts = events, [], [], []
        self.closed = self.interrupted = False
        self.id = "codex-thread"
        self._client = self

    async def request(self, *args, **kwargs):
        return SimpleNamespace(config=SimpleNamespace(model_dump=lambda: {"mcp_servers": {"unrelated": {"command": "false"}}}))

    async def __aenter__(self):
        return self

    async def thread_start(self, **options):
        self.starts.append(options)
        return self

    async def thread_resume(self, sid, **options):
        self.resumes.append(sid)
        return self

    async def turn(self, prompt, **options):
        self.prompts.append((prompt, options))
        return self

    async def stream(self):
        for ev in self.events:
            if isinstance(ev, Exception):
                raise ev
            yield ev

    async def interrupt(self):
        self.interrupted = True

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_streams_complete_messages_once_and_resumes(monkeypatch):
    message = event("item/completed", item={"id": "m", "type": "agentMessage", "text": "Hello"})
    client = Client([
        event("item/agentMessage/delta", itemId="m", delta="Hel"),
        event("item/agentMessage/delta", itemId="m", delta="lo"),
        event("item/started", item={"id": "c", "type": "commandExecution", "command": "pwd"}),
        message, message,
        event("turn/completed", turn={"status": "completed"}),
    ])
    monkeypatch.setattr(codex_brain, "codex_client", lambda **kw: client)
    session = CodexBrain().session(cwd=".", system=lambda: "Ken's memory", resume="saved-codex-id")
    text, delta, tools = [], [], []
    async def on_text(t): text.append(t)
    async def on_delta(t): delta.append(t)
    async def on_tool(t): tools.append(t)
    assert await session.ask("hello", on_text, on_tool, on_delta) == "Hello"
    assert text == ["Hello"] and delta == ["Hel", "Hello"]
    assert tools == ["Shell · pwd"]
    assert client.resumes == ["saved-codex-id"] and not client.starts
    assert session.session_id == "codex-thread" and not session.busy
    await session.close()
    assert client.closed


@pytest.mark.asyncio
async def test_failed_turn_does_not_replay_side_effects(monkeypatch):
    client = Client([
        event("item/completed", item={"id": "m", "type": "agentMessage", "text": "Saved it"}),
        event("turn/completed", turn={"status": "failed", "error": {"message": "connection lost"}}),
    ])
    monkeypatch.setattr(codex_brain, "codex_client", lambda **kw: client)
    session = CodexBrain().session(cwd=".", system="")
    async def on_text(t): pass
    with pytest.raises(RuntimeError, match="connection lost"):
        await session.ask("save", on_text)
    assert len(client.prompts) == 1 and client.closed and not session.busy
    assert client.starts[0]["config"]["mcp_servers"] == {"unrelated": {"enabled": False}}


@pytest.mark.asyncio
async def test_cancel_interrupts_the_running_turn(monkeypatch):
    entered = asyncio.Event()
    client = Client([])
    async def stream():
        entered.set()
        await asyncio.Event().wait()
        yield
    client.stream = stream
    monkeypatch.setattr(codex_brain, "codex_client", lambda **kw: client)
    session = CodexBrain().session(cwd=".", system="")
    async def on_text(t): pass
    task = asyncio.create_task(session.ask("work", on_text))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.interrupted and not session.busy
    await session.close()


@pytest.mark.asyncio
async def test_close_waits_for_warmup(monkeypatch):
    entered, ready = asyncio.Event(), asyncio.Event()
    client = Client([])
    async def start(**options):
        entered.set()
        await ready.wait()
        return client
    client.thread_start = start
    monkeypatch.setattr(codex_brain, "codex_client", lambda **kw: client)
    session = CodexBrain().session(cwd=".", system="")
    warm = asyncio.create_task(session.warm())
    await entered.wait()
    close = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    assert not client.closed
    ready.set()
    await asyncio.gather(warm, close)
    assert client.closed and session.client is None


def test_external_mcp_translation_preserves_credentials():
    assert mcp_config({"browser": {"type": "stdio", "command": "node", "args": ["server.js"], "env": {"KEY": "secret"}}}) == {
        "browser": {"command": "node", "args": ["server.js"], "env": {"KEY": "secret"}, "enabled": True}}
    assert mcp_config({"remote": {"type": "http", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer secret"}}})["remote"]["http_headers"] == {"Authorization": "Bearer secret"}
    with pytest.raises(ValueError, match="legacy SSE"):
        mcp_config({"old": {"type": "sse", "url": "https://example.com/sse"}})


@pytest.mark.asyncio
async def test_codex_onboarding_reuses_account_without_touching_claude_credentials(tmp_path, monkeypatch):
    from onboarding import CodexOnboarding

    class AccountClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def account(self): return SimpleNamespace(account={"type": "chatgpt"}, requires_openai_auth=True)

    monkeypatch.setattr(codex_brain, "codex_client", lambda **kw: AccountClient())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    saved = "CLAUDE_CODE_OAUTH_TOKEN=existing\n"
    (tmp_path / ".env").write_text(saved)
    calls = []
    async def connected(): calls.append(True)
    setup = CodexOnboarding(tmp_path, connected, lambda: True)
    await setup.refresh()
    assert setup.status()["connected"] and setup.status()["backend"] == "codex"
    assert (await setup.start())["connected"] and calls == [True]
    assert (tmp_path / ".env").read_text() == saved


@pytest.mark.asyncio
async def test_codex_sign_in_waits_for_browser_completion(tmp_path, monkeypatch):
    from onboarding import CodexOnboarding

    ready = asyncio.Event()
    clients = []
    class LoginClient:
        closed = False
        auth_url = "https://example.com/login"
        async def __aenter__(self): return self
        async def __aexit__(self, *args): await self.close()
        async def account(self): return SimpleNamespace(account=None, requires_openai_auth=True)
        async def login_chatgpt(self): return self
        async def wait(self):
            await ready.wait()
            return SimpleNamespace(success=True, error=None)
        async def close(self): self.closed = True
    def make_client(**kw):
        client = LoginClient()
        clients.append(client)
        return client
    monkeypatch.setattr(codex_brain, "codex_client", make_client)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = []
    async def connected(): calls.append(True)
    setup = CodexOnboarding(tmp_path, connected, lambda: True)
    assert (await setup.start())["url"] == LoginClient.auth_url
    assert setup.active and not setup.status()["connected"]
    ready.set()
    await setup.watch
    assert setup.status()["connected"] and not setup.active and calls == [True]
    assert all(c.closed for c in clients)


def test_api_key_provider_does_not_replace_saved_login(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-secret")
    client = codex_brain.codex_client()
    config = client._client._sync.config
    assert 'model_provider="ken_openai"' in config.config_overrides
    assert 'model_providers.ken_openai.env_key="OPENAI_API_KEY"' in config.config_overrides
    assert not any("test-key" in value for value in config.config_overrides)


@pytest.mark.asyncio
async def test_standard_mcp_bridge_exposes_only_enabled_tools(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from connections import Connections

    connections = Connections(tmp_path)
    connections.save({"emails": [{"address": "test@example.com", "password": "unused", "imap_host": "example.com",
                                  "smtp_host": "example.com", "smtp_port": 465}],
                      "calendars": [{"name": "Private", "url": "https://example.com/calendar"}]})
    spec = connections.mcp({"email"}, backend="codex")["ken"]
    async with stdio_client(StdioServerParameters(command=spec["command"], args=spec["args"])) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert {t.name for t in listing.tools} == {"email_search", "email_read", "email_draft", "email_send"}
            # Schema validation is shared with Claude; no network request is made.
            result = await session.call_tool("email_read", {})
            assert result.is_error
