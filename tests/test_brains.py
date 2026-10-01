"""Session lifecycle races without launching a CLI or calling a model."""
import asyncio

from brains import ClaudeBrain, ClaudeSession


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
