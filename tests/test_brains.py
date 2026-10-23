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


async def test_a_dead_claude_code_is_started_again_once_before_it_did_anything():
    from claude_agent_sdk import CLIConnectionError, ResultMessage

    connects, shown = [], []

    class Dead:
        async def query(self, prompt):
            raise CLIConnectionError("Cannot write to terminated process (exit code: 143)")

        async def disconnect(self):
            pass

    class Alive(Dead):
        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id='s', result='posted')

    session = ClaudeSession(ClaudeBrain(), '.', '', None)
    session.client = Dead()

    async def connect(resume):
        connects.append(resume)
        session.client = Alive()
    session._connect = connect

    async def on_text(text):
        shown.append(text)

    assert await session.ask('post it', on_text) == 'posted' and shown == ['posted'] and connects == [None]


async def test_no_second_try_once_claude_code_started_working():
    import pytest
    from claude_agent_sdk import AssistantMessage, ProcessError, TextBlock

    class DiesMidway:
        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield AssistantMessage(content=[TextBlock(text='Posting now')], model='m')
            raise ProcessError("Command failed", exit_code=143)

        async def disconnect(self):
            pass

    session = ClaudeSession(ClaudeBrain(), '.', '', None)
    session.client = DiesMidway()
    session.stderr.append("fatal: killed")

    async def connect(resume):
        raise AssertionError("must not start again: the post may already be out")
    session._connect = connect

    async def on_text(text):
        pass

    with pytest.raises(ProcessError):
        await session.ask('post it', on_text)


def test_ken_ignores_this_computers_own_claude_code_setup():
    import claude_agent_sdk

    seen = {}

    class Client:
        def __init__(self, options):
            seen['options'] = options

        async def connect(self):
            pass

    session = ClaudeSession(ClaudeBrain(), '.', '', None)
    real = claude_agent_sdk.ClaudeSDKClient
    claude_agent_sdk.ClaudeSDKClient = Client
    try:
        asyncio.run(session._connect(None))
    finally:
        claude_agent_sdk.ClaudeSDKClient = real
    o = seen['options']
    assert o.setting_sources == [] and o.strict_mcp_config and o.max_buffer_size >= 32 * 1024 * 1024
    assert o.stderr is not None


async def test_session_is_saved_before_completion_and_error_results_fail():
    import pytest
    from claude_agent_sdk import ResultMessage, SystemMessage
    class Client:
        async def query(self, prompt):
            pass
        async def receive_response(self):
            yield SystemMessage(subtype='init', data={'session_id': 'early-session'})
            assert session.session_id == 'early-session'
            yield ResultMessage(subtype='error_max_turns', duration_ms=1, duration_api_ms=1,
                                is_error=True, num_turns=1, session_id='early-session', result='Turn limit reached')
    session = ClaudeSession(ClaudeBrain(), '.', '', None)
    session.client = Client()
    async def text(value):
        raise AssertionError('An error is not a successful result')
    with pytest.raises(RuntimeError, match='Turn limit'):
        await session.ask('work', text)
    assert session.session_id == 'early-session'
