"""Nothing hangs: an AI that fails or never answers ends the turn in seconds with plain words and a way out."""

import asyncio
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine as engine_mod  # noqa: E402
from brains import AIUnavailable, ClaudeBrain, ClaudeSession, Session, give_up  # noqa: E402
from codex_brain import error_status  # noqa: E402
from engine import HOME, Engine, NoAnswer, explain_failure, until_idle  # noqa: E402
from test_engine import FakeBrain, FakeSession  # noqa: E402


def retry(attempt, status, error, delay_ms=500):
    from claude_agent_sdk import SystemMessage

    return SystemMessage(subtype="api_retry", data={"type": "system", "subtype": "api_retry", "attempt": attempt, "max_retries": 10,
                                                    "retry_delay_ms": delay_ms, "error_status": status, "error": error})


class RetryingClient:
    """Claude Code meeting a failing AI: it retries with growing waits until it's interrupted (as it really does)."""

    def __init__(self, status, error, delays=(500, 1200, 2400, 4200, 9400, 16000, 33000)):
        self.status, self.error, self.delays = status, error, delays
        self.interrupted = asyncio.Event()

    async def query(self, prompt):
        pass

    async def interrupt(self):
        self.interrupted.set()

    async def receive_messages(self):
        from claude_agent_sdk import ResultMessage, SystemMessage

        yield SystemMessage(subtype="init", data={"subtype": "init", "session_id": "s"})
        for n, delay in enumerate(self.delays, 1):
            yield retry(n, self.status, self.error, delay)
            try:
                await asyncio.wait_for(self.interrupted.wait(), delay / 1000 / 50)  # 50x faster than real life
                break
            except asyncio.TimeoutError:
                pass
        yield ResultMessage(subtype="error_during_execution", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=0,
                            session_id="s", result="", terminal_reason="aborted_streaming")


def session_with(client):
    session = ClaudeSession(ClaudeBrain(), ".", "", None)
    session.client, session.model = client, ""
    return session


async def nothing(_):
    pass


@pytest.mark.parametrize("status,error", [(401, "authentication_failed"), (403, "unknown"), (402, "billing_error")])
async def test_a_refused_ai_request_stops_at_once(status, error):
    client = RetryingClient(status, error)
    session = session_with(client)
    with pytest.raises(AIUnavailable) as caught:
        await session.ask("hi", nothing)
    assert caught.value.status == status and client.interrupted.is_set()
    assert not session.busy


@pytest.mark.parametrize("status,error", [(529, "overloaded"), (500, "api_error"), (None, "unknown")])
async def test_a_busy_or_unreachable_ai_gets_a_few_quick_retries_then_stops(status, error, monkeypatch):
    import brains

    monkeypatch.setattr(brains, "time", SimpleNamespace(monotonic=fast_clock()))
    client = RetryingClient(status, error)
    session = session_with(client)
    with pytest.raises(AIUnavailable):
        await session.ask("hi", nothing)
    assert client.interrupted.is_set()


def fast_clock(scale=50):
    """Time passing 50x faster, to match RetryingClient's sped-up waits."""
    real = time.monotonic
    start = real()
    return lambda: start + (real() - start) * scale


def test_when_to_stop_retrying():
    assert give_up(401, "authentication_failed", 0, 0.5)
    assert give_up(None, "billing_error", 0, 0.5)
    assert not give_up(529, "overloaded", 0, 0.5)
    assert not give_up(529, "overloaded", 8, 4)
    assert give_up(529, "overloaded", 8, 16)  # the next wait would pass 20 seconds
    assert give_up(None, "unknown", 21, 0)


def test_codex_error_status():
    assert error_status("unauthorized") == 401
    assert error_status("serverOverloaded") == 529
    assert error_status({"httpConnectionFailed": {"httpStatusCode": 502}}) == 502
    assert error_status({"responseStreamDisconnected": {"httpStatusCode": None}}) is None
    assert error_status(None) is None


def included(monkeypatch, on=True):
    import onboarding

    monkeypatch.setattr(onboarding, "included_ai_in_use", lambda: on)


def test_one_plain_sentence_and_one_button_for_every_failure(monkeypatch):
    included(monkeypatch, False)
    assert explain_failure(AIUnavailable(401, "authentication_failed"), "claude") == ("Your Claude connection stopped working.", "reconnect")
    assert explain_failure(RuntimeError("Not logged in · Please run /login"), "claude") == ("Your Claude connection stopped working.", "reconnect")
    assert explain_failure(AIUnavailable(401, "401 Unauthorized"), "codex") == ("Your ChatGPT connection stopped working.", "reconnect")
    assert explain_failure(AIUnavailable(529, "overloaded"), "claude") == ("Claude isn't answering just now.", "retry")
    assert explain_failure(AIUnavailable(None, "unknown"), "codex") == ("ChatGPT isn't answering just now.", "retry")
    assert explain_failure(AIUnavailable(429, "rate_limit"), "claude") == ("You've reached your Claude plan's limit for now.", "retry")
    assert explain_failure(AIUnavailable(402, "billing_error"), "claude") == ("Your Claude account is out of credit.", "reconnect")
    assert explain_failure(NoAnswer(), "claude") == ("Claude didn't answer, so I stopped waiting.", "retry")
    assert explain_failure(ValueError("list index out of range"), "claude") == ("Something went wrong on Ken's side.", "retry")


def test_included_ai_failures_never_blame_the_person(monkeypatch):
    included(monkeypatch)
    for e in (AIUnavailable(401, "authentication_failed"), RuntimeError("Not logged in · Please run /login"),
              AIUnavailable(529, "overloaded"), AIUnavailable(None, "unknown"), NoAnswer()):
        assert explain_failure(e, "claude") == ("Ken couldn't reach its AI just now.", "retry")
    assert explain_failure(AIUnavailable(402, "billing_error"), "claude") == ("You've used this month's included AI.", "connect")
    assert explain_failure(RuntimeError("API Error: 402 Out of included AI"), "claude")[1] == "connect"


# What Claude Code, the SDKs and the services really say when things fail: none of it may reach a person.
RAW = [
    "Not logged in · Please run /login",
    "Invalid API key · Please run /login",
    'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}',
    'API Error: 402 {"type":"error","error":{"type":"billing_error","message":"Out of included AI"}}',
    'API Error: 403 {"type":"error","error":{"type":"permission_error","message":"OAuth token has been revoked"}}',
    "API Error: Repeated 529 Overloaded errors. The API is at capacity — check your inference gateway (127.0.0.1:8802).",
    'API Error: 500 {"type":"error","error":{"type":"api_error","message":"Internal server error"}}',
    "API Error: Request timed out.",
    "API Error: Connection error.",
    "Claude AI usage limit reached|1793750400",
    "Credit balance is too low",
    '{"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The \'gpt-6\' model is not supported when using Codex with a ChatGPT account."}}',
    "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header",
    "stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses)",
    "Command failed with exit code 1 (exit code: 1)\nError output: Check stderr output for details",
    "Traceback (most recent call last):\n  File \"brains.py\", line 1\nKeyError: 'session_id'",
    "Claude Code ended without completing the turn (error_max_turns).",
]
BANNED = re.compile(r"/login|\brun |API|x-api-key|\b[1-5]\d\d\b|claude-[a-z]|gpt-|Traceback|stderr|\{")


def clean(text):
    return not BANNED.search(text)


@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize("brain", ["claude", "codex"])
def test_no_raw_error_wording_ever(monkeypatch, hosted, brain):
    included(monkeypatch, hosted)
    errors = [RuntimeError(r) for r in RAW] + [AIUnavailable(c, r) for c in (None, 400, 401, 402, 403, 404, 429, 500, 529) for r in RAW[:3]] + [NoAnswer()]
    for e in errors:
        text, action = explain_failure(e, brain)
        assert clean(text), (str(e), text)
        assert action in ("retry", "connect", "reconnect")


class SilentSession(Session):
    """An AI that never says anything (the request hangs)."""
    heard = False

    async def ask(self, *a, **k):
        await asyncio.sleep(3600)


async def test_a_turn_with_no_answer_at_all_is_stopped():
    started = time.monotonic()
    with pytest.raises(NoAnswer):
        await until_idle(SilentSession(), SilentSession().ask(), first=0.2)
    assert time.monotonic() - started < 6


async def test_long_work_is_not_cut_short_once_the_ai_answered():
    class Working(Session):
        heard = True

        async def ask(self):
            await asyncio.sleep(0.4)
            return "done"

    s = Working()
    s.last_activity = time.monotonic()
    assert await until_idle(s, s.ask(), first=0.1) == "done"


class FailingSession(FakeSession):
    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        self.brain.asks.append({"prompt": prompt})
        if len(self.brain.asks) == 1:
            raise AIUnavailable(529, "overloaded")
        await on_text("here you go")
        return "here you go"


class FlakyBrain(FakeBrain):
    def session(self, *, cwd, system, resume=None, model_fn=None, mcp_fn=None, env_fn=None):
        return FailingSession(self, cwd, system, resume, model_fn, mcp_fn, env_fn)


@pytest.fixture
def flaky(tmp_path):
    e = Engine(tmp_path, FlakyBrain())
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    e.agents_dir.mkdir()
    e.requests_dir.mkdir()
    return e


async def test_a_failed_reply_says_so_and_try_again_asks_the_same_thing(flaky, tmp_path, aiohttp_client):
    from web import WebApp

    flaky.log_message(HOME, "you", "what's 17 x 23?")
    assert not await flaky.send_message(HOME, "what's 17 x 23?", log_user=False)
    failed = flaky.messages(HOME)[-1]
    assert failed["role"] == "ken" and failed["error"] and failed["action"] == "retry"
    assert failed["text"] == "Claude isn't answering just now."

    app = WebApp(flaky, tmp_path, rev="abc")
    app._brain_connected = lambda pid: True
    client = await aiohttp_client(app.build())
    await client.get(f"/login?token={app.token}", allow_redirects=False)
    assert (await client.post(f"/api/chats/{HOME}/retry", json={})).status == 200
    for _ in range(50):
        await asyncio.sleep(0.02)
        if flaky.messages(HOME)[-1]["text"] == "here you go":
            break
    said = [(m["role"], m["text"]) for m in flaky.messages(HOME)]
    assert said[-1] == ("ken", "here you go")
    assert [t for r, t in said if r == "you"] == ["what's 17 x 23?"]  # asked again, not written twice
    assert "what's 17 x 23?" in flaky.brain.asks[-1]["prompt"]
    assert (await client.post(f"/api/chats/{HOME}/retry", json={})).status == 409  # nothing failed now


async def test_a_chat_the_ai_never_answers_is_stopped_with_a_reason(monkeypatch, flaky):
    monkeypatch.setattr(engine_mod, "NO_ANSWER_LIMIT", 0.2)

    class Silent(FakeSession):
        heard = False

        async def ask(self, *a, **k):
            await asyncio.sleep(3600)

    flaky.brain.session = lambda **kw: Silent(flaky.brain, kw["cwd"], kw["system"], None)
    started = time.monotonic()
    assert not await flaky.send_message(HOME, "hello")
    assert time.monotonic() - started < 10
    assert "didn't answer" in flaky.messages(HOME)[-1]["text"]


async def test_codex_stops_retrying_a_refused_request():
    from codex_brain import CodexBrain, CodexSession

    class Event:
        def __init__(self, method, payload):
            self.method, self.payload = method, SimpleNamespace(model_dump=lambda **k: payload)

    class Turn:
        interrupted = False

        async def stream(self):
            for _ in range(5):
                yield Event("error", {"error": {"message": "401 Unauthorized", "codexErrorInfo": "unauthorized"}, "willRetry": True})
            raise AssertionError("kept waiting")

        async def interrupt(self):
            Turn.interrupted = True

    class Thread:
        async def turn(self, prompt, model=None):
            return Turn()

    class Client:
        async def close(self):
            pass

    session = CodexSession(CodexBrain(), ".", "", None)
    session.client, session.thread = Client(), Thread()
    with pytest.raises(AIUnavailable) as caught:
        await session.ask("hi", nothing)
    assert caught.value.status == 401 and Turn.interrupted and not session.heard


@pytest.mark.parametrize("hosted", [False, True])
async def test_what_reaches_the_chat_screen_is_always_plain(monkeypatch, tmp_path, aiohttp_client, hosted):
    """Every raw failure, through a real chat turn and the app's own API: the conversation the screen shows never
    contains Claude Code / SDK wording, status codes or model ids."""
    from web import WebApp

    included(monkeypatch, hosted)
    raw = iter(RAW)

    class Raw(FakeSession):
        async def ask(self, *a, **k):
            raise RuntimeError(next(raw))

    brain = FakeBrain()
    brain.session = lambda **kw: Raw(brain, kw["cwd"], kw["system"], None)
    e = Engine(tmp_path, brain)
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    e.agents_dir.mkdir()
    e.requests_dir.mkdir()
    for _ in RAW:
        assert not await e.send_message(HOME, "hello")
    app = WebApp(e, tmp_path, rev="abc")
    client = await aiohttp_client(app.build())
    await client.get(f"/login?token={app.token}", allow_redirects=False)
    shown = [m for m in (await (await client.get(f"/api/chats/{HOME}")).json())["messages"] if m["role"] == "ken"]
    assert len(shown) == len(RAW)
    for m in shown:
        assert clean(m["text"]), m["text"]
        assert m["action"] in ("retry", "connect", "reconnect")


async def test_ken_speaking_first_that_fails_leaves_no_error_without_a_question(flaky):
    """Ken's hello on a new install hits a failing AI: nothing to answer or retry, so the chat stays empty (and the
    person's first message then gets the plain error and Try again)."""
    assert not await flaky.send_message(HOME, "", log_user=False, via="app", prompt="say hello")
    assert flaky.messages(HOME) == []
