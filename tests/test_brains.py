"""Session lifecycle races without launching a CLI or calling a model."""
import asyncio

from types import SimpleNamespace

from brains import Brain, Brains, ClaudeBrain, ClaudeSession, CodexBrain, CodexSession, Session, describe_codex_item


async def test_clear_during_warmup_disconnects_the_connecting_client():
    entered, ready = asyncio.Event(), asyncio.Event()
    events = []

    class Client:
        async def disconnect(self):
            events.append('disconnected')

    session = ClaudeSession(ClaudeBrain(), '.', '', None)

    async def connect(resume):
        session.client = Client()
        entered.set()
        await ready.wait()
        events.append('connected')

    session._connect = connect
    warm = asyncio.create_task(session.warm())
    await entered.wait()
    close = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    assert not close.done()
    ready.set()
    await asyncio.gather(warm, close)
    assert events == ['connected', 'disconnected']
    assert session.client is None


async def test_model_too_new_for_claude_code_falls_back_to_the_default():
    from claude_agent_sdk import ResultMessage

    error = "API Error: 400 Claude Code 2.1.238 does not support this model; version 2.1.280 or newer is required."
    models, shown = [], []

    class Client:
        def __init__(self):
            self.model = 'claude-new'

        async def set_model(self, model):
            self.model = model

        async def query(self, prompt):
            models.append(self.model)

        async def receive_response(self):
            text = error if self.model == 'claude-new' else 'hi'
            yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id='s', result=text)

    session = ClaudeSession(ClaudeBrain(lambda: 'claude-new'), '.', '', None)
    session.client, session.model = Client(), 'claude-new'

    async def on_text(text):
        shown.append(text)

    assert await session.ask('hello', on_text) == 'hi'
    assert shown == ['hi']  # the Claude Code error never reaches the chat
    assert models == ['claude-new', None]
    # The next message goes straight to the default instead of failing first.
    assert await session.ask('again', on_text) == 'hi'
    assert models == ['claude-new', None, None]


class Recorder(Session):
    def __init__(self, brain, resume, system):
        self.brain, self.session_id, self.system, self.closed = brain, resume, system, False

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        self.session_id = self.session_id or f"{self.brain.name}-new"
        return self.brain.name

    async def close(self):
        self.closed = True


class StubBrain(Brain):
    def __init__(self, name, prefix):
        self.name, self.prefix, self.opened = name, prefix, []

    def session(self, *, cwd, system, resume=None, model_fn=None, recap=None):
        self.opened.append(Recorder(self, resume, system))
        return self.opened[-1]

    def owns(self, model):
        return model.startswith(self.prefix)


async def test_each_chat_runs_on_the_brain_that_owns_its_model():
    claude, codex = StubBrain("claude", "claude-"), StubBrain("codex", "gpt-")
    brains = Brains([claude, codex])
    model = ["claude-opus"]
    session = brains.session(cwd=".", system="soul", resume="old-claude-id", model_fn=lambda: model[0],
                             recap=lambda: "RECENT")
    assert await session.ask("hi", None) == "claude"
    assert claude.opened[0].session_id == "old-claude-id"  # ids from before a second brain still resume
    assert session.session_id == "old-claude-id"

    model[0] = "gpt-6"
    assert await session.ask("hi", None) == "codex"
    assert claude.opened[0].closed
    assert codex.opened[0].session_id == "codex-new"  # a fresh session: Claude's can't resume in Codex
    assert codex.opened[0].system().endswith("RECENT")  # it gets the thread so far instead
    assert session.session_id == "codex:codex-new"

    # Saved and reopened, the chat resumes on Codex.
    again = brains.session(cwd=".", system="soul", resume=session.session_id, model_fn=lambda: "gpt-6")
    await again.ask("hi", None)
    assert codex.opened[-1].session_id == "codex-new"


def test_default_brain_is_the_first_one_signed_in():
    claude, codex = StubBrain("claude", "claude-"), StubBrain("codex", "gpt-")
    claude.connected = lambda: False
    brains = Brains([claude, codex])
    assert brains.brain_for("") is codex
    brains.catalog = {"codex": [["o-something", "O"]]}
    assert brains.brain_for("o-something") is codex  # the live list decides before prefixes


def test_codex_items_read_like_claude_tools():
    cmd = SimpleNamespace(type="commandExecution", command="/bin/zsh -lc 'npm test'")
    assert describe_codex_item(cmd) == "Bash · npm test"
    edit = SimpleNamespace(type="fileChange", changes=[SimpleNamespace(path="/x/app.py"), SimpleNamespace(path="/x/b.py")])
    assert describe_codex_item(edit) == "Edit · app.py +1"
    assert describe_codex_item(SimpleNamespace(type="reasoning")) == ""


async def test_codex_streams_deltas_tools_and_the_final_answer():
    def ev(method, **payload):
        return SimpleNamespace(method=method, payload=SimpleNamespace(**payload))

    message = SimpleNamespace(type="agentMessage", text="All green.")
    events = [
        ev("item/started", item=SimpleNamespace(type="commandExecution", command="/bin/zsh -lc 'npm test'")),
        ev("item/agentMessage/delta", item_id="m1", delta="All "),
        ev("item/agentMessage/delta", item_id="m1", delta="green."),
        ev("item/completed", item=message),
        ev("turn/completed", turn=SimpleNamespace(status=SimpleNamespace(value="completed"), error=None)),
    ]

    class Turn:
        async def stream(self):
            for e in events:
                yield e

    class Thread:
        async def turn(self, prompt, model=None):
            return Turn()

    session = CodexSession(CodexBrain(), ".", "", None)
    session.codex, session.thread = object(), Thread()
    shown, tools, deltas = [], [], []

    async def collect(into):
        async def add(x):
            into.append(x)
        return add

    final = await session.ask("run the tests", await collect(shown), await collect(tools), await collect(deltas))
    assert final == "All green." and shown == ["All green."]
    assert tools == ["Bash · npm test"] and deltas == ["All ", "All green."]
    assert not session.busy


async def test_warming_up_mid_reply_keeps_the_reply_running():
    claude, codex = StubBrain("claude", "claude-"), StubBrain("codex", "gpt-")
    brains = Brains([claude, codex])
    model, answering, release = ["claude-opus"], asyncio.Event(), asyncio.Event()
    session = brains.session(cwd=".", system="soul", model_fn=lambda: model[0])

    async def slow(prompt, on_text, on_tool=None, on_delta=None):
        answering.set()
        await release.wait()
        return "claude"

    await session.warm()
    claude.opened[0].ask = slow
    reply = asyncio.create_task(session.ask("hi", None))
    await answering.wait()
    model[0] = "gpt-6"  # picked mid-reply, then the chat is reopened
    await session.warm()
    assert not claude.opened[0].closed and not codex.opened and session.busy
    release.set()
    assert await reply == "claude"
    assert await session.ask("next", None) == "codex"  # the new model takes the next task
