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
