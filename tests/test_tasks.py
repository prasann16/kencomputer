"""Exercise concurrent workers and controls without model calls or timing guesses."""
import asyncio
import json
import subprocess

import pytest
import pytest_asyncio

from engine import Engine
from tasks import TaskManager
from test_engine import FakeBrain, FakeSession


class Worker(FakeSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = asyncio.Event()
        self.closing = asyncio.Event()
        self.closed = asyncio.Event()
        self.close_gate = None
        self.tools = {}
        self.turns = 0

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        if getattr(self, 'supervisor', False):
            return await super().ask(prompt, on_text, on_tool, on_delta)
        if not self.turns:
            self.instructions = json.loads(prompt)
            self.tools = self.mcp_fn()['task-report']
            self.session_id = self.session_id or f"worker-{len(self.brain.workers)}"
            self.on_session_id(self.session_id)
            self.brain.workers.append(self)
        self.turns += 1
        self.busy = True
        try:
            await self.release.wait()
            return 'Unverified result'
        finally:
            self.busy = False

    async def call(self, name, **args):
        return await self.tools[name].handler(args)

    async def acknowledge(self):
        return await self.call('task_checkpoint', progress='Working on the current instructions')

    async def report(self, status='ready', summary='Saved the work', evidence='artifact.txt'):
        return await self.call('report_task', status=status, summary=summary, evidence=evidence)

    async def interrupt(self):
        self.release.set()

    async def close(self):
        self.closing.set()
        if self.close_gate:
            await self.close_gate.wait()
        self.closed.set()


class Brain(FakeBrain):
    def __init__(self):
        super().__init__()
        self.workers = []

    def session(self, **kw):
        return Worker(self, **kw)


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.001)


@pytest_asyncio.fixture
async def engine(tmp_path, monkeypatch):
    import task_tools
    monkeypatch.setattr(task_tools, 'create_sdk_mcp_server', lambda **kw: {t.name: t for t in kw['tools']})
    e = Engine(tmp_path, Brain())
    e.configure_home(tmp_path / 'work', lambda: 'KEN', lambda: 'KEN AT WORK')
    e.agents_dir.mkdir()
    e.requests_dir.mkdir()
    yield e
    await e.tasks.close()
    e.db.close()


async def start(engine, title='Research', **kw):
    task = engine.tasks.create(title, 'Do the work', 'Show evidence', **kw)
    await eventually(lambda: task['id'] in engine.tasks.sessions and bool(engine.tasks.sessions[task['id']].tools))
    return task, engine.tasks.sessions[task['id']]


async def test_two_workers_do_not_block_main_and_snapshot_is_fresh(engine):
    a, wa = await start(engine, 'Research')
    b, wb = await start(engine, 'Code')
    await asyncio.wait_for(engine.send_message('home', 'How are both jobs doing?'), 1)
    prompt = engine.brain.asks[-1]['prompt']
    assert a['id'] in prompt and b['id'] in prompt and 'running' in prompt
    assert not wa.release.is_set() and not wb.release.is_set()
    assert wa.cwd != wb.cwd


async def test_steering_fences_old_reports_and_waits_for_teardown(engine):
    t, first = await start(engine)
    _, other = await start(engine, 'Other')
    await first.acknowledge()
    first.close_gate = asyncio.Event()
    changed = engine.tasks.control(t['id'], 'steer', 'Use the new direction', expected_revision=1)
    assert changed['revision'] == 2 and changed['applied_revision'] == 1
    assert (await first.report(summary='Stale success'))['isError']
    await first.closing.wait()
    assert engine.tasks.sessions[t['id']] is first and not other.release.is_set()
    # A second control during teardown must not cancel cleanup or the next worker.
    engine.tasks.control(t['id'], 'steer', 'Also use this constraint', expected_revision=2)
    first.close_gate.set()
    await eventually(lambda: len(engine.brain.workers) == 3)
    second = engine.tasks.sessions[t['id']]
    assert first.closed.is_set() and second.session_id == first.session_id
    assert second.instructions['revision'] == 3
    assert [c['instruction'] for c in second.instructions['changes']] == ['Use the new direction', 'Also use this constraint']
    assert engine.tasks.get(t['id'])['applied_revision'] == 1
    await second.acknowledge()
    assert engine.tasks.get(t['id'])['applied_revision'] == 3
    assert engine.tasks.get(t['id'], details=True)['commands'][-1]['state'] == 'applied'


async def test_stop_queued_and_rapid_steer_before_start(engine):
    stopped = engine.tasks.create('Stop me', 'Work')
    engine.tasks.control(stopped['id'], 'stop')
    steered = engine.tasks.create('Change me', 'Original')
    engine.tasks.control(steered['id'], 'steer', 'New plan')
    await eventually(lambda: engine.tasks.get(stopped['id'])['state'] == 'stopped' and len(engine.brain.workers) == 1)
    assert engine.tasks.get(stopped['id'])['attempt'] == 0
    assert engine.brain.workers[0].instructions['revision'] == 2


async def test_pause_resume_and_retry_keep_identity_and_history(engine):
    t, first = await start(engine)
    await first.acknowledge()
    engine.tasks.control(t['id'], 'pause')
    await eventually(lambda: engine.tasks.get(t['id'])['state'] == 'paused')
    engine.tasks.control(t['id'], 'resume')
    await eventually(lambda: len(engine.brain.workers) == 2)
    second = engine.brain.workers[-1]
    assert second.session_id == first.session_id
    await second.acknowledge()
    await second.report('failed', 'A dependency failed')
    second.release.set()
    await eventually(lambda: t['id'] not in engine.tasks.drivers)
    engine.tasks.control(t['id'], 'retry')
    await eventually(lambda: len(engine.brain.workers) == 3)
    third = engine.brain.workers[-1]
    assert third.session_id != second.session_id
    assert third.instructions['previous_result'] == 'A dependency failed'
    assert len(engine.tasks.get(t['id'], details=True)['attempts']) == 3


async def test_completion_requires_independent_review_with_evidence(engine):
    t, worker = await start(engine)
    assert (await worker.report())['isError']  # acknowledge first
    await worker.acknowledge()
    assert (await worker.report('passed'))['isError']  # work cannot verify itself
    await worker.report()
    assert engine.tasks.get(t['id'])['summary'] == 'Saved the work'  # durable before result
    worker.release.set()
    await eventually(lambda: t['id'] not in engine.tasks.drivers)
    assert engine.tasks.get(t['id'])['state'] == 'needs_review'
    assert worker.turns == 1
    assert worker.env_fn()['CLAUDE_CODE_DISABLE_BACKGROUND_TASKS'] == '1'
    engine.tasks.control(t['id'], 'review')
    await eventually(lambda: len(engine.brain.workers) == 2)
    review = engine.brain.workers[-1]
    assert review.session_id != worker.session_id
    assert review.instructions['mode'] == 'review'
    await review.acknowledge()
    assert (await review.report('passed', evidence=''))['isError']
    await review.report('passed', evidence='Inspected artifact.txt; checks passed')
    review.release.set()
    await eventually(lambda: t['id'] not in engine.tasks.drivers)
    assert engine.tasks.get(t['id'])['state'] == 'done'


async def test_no_report_is_unverified_and_waiting_is_not_success(engine):
    t, worker = await start(engine)
    worker.release.set()
    await eventually(lambda: t['id'] not in engine.tasks.drivers)
    assert engine.tasks.get(t['id'])['state'] == 'needs_review'
    assert worker.turns == 3
    other, worker = await start(engine)
    await worker.acknowledge()
    await worker.report('waiting', 'Which folder should I use?')
    worker.release.set()
    await eventually(lambda: other['id'] not in engine.tasks.drivers)
    assert engine.tasks.get(other['id'])['state'] == 'waiting'


async def test_revision_conflicts_and_start_idempotency(engine):
    t = engine.tasks.create('One', 'Work', request_key='same-message')
    assert engine.tasks.create('One', 'Work', request_key='same-message')['id'] == t['id']
    engine.tasks.control(t['id'], 'steer', 'New plan', expected_revision=1)
    with pytest.raises(ValueError, match='changed'):
        engine.tasks.control(t['id'], 'stop', expected_revision=1)
    assert engine.tasks.get(t['id'])['state'] == 'queued'


async def test_resource_waiters_leave_worker_capacity_and_lease_lasts_through_close(engine):
    engine.tasks.slots = asyncio.Semaphore(2)
    first, owner = await start(engine, resources=['browser'])
    waiter = engine.tasks.create('Browser two', 'Work', resources=['browser'])
    _, independent = await start(engine, 'Independent')
    assert engine.tasks.get(waiter['id'])['state'] == 'queued'
    owner.close_gate = asyncio.Event()
    engine.tasks.control(first['id'], 'stop')
    await owner.closing.wait()
    assert len(engine.brain.workers) == 2
    owner.close_gate.set()
    await eventually(lambda: len(engine.brain.workers) == 3)
    assert owner.closed.is_set() and not independent.release.is_set()


async def test_git_tasks_use_separate_worktrees_and_preserve_source(engine, tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    git('init')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'Initial')
    (repo / 'local.txt').write_text('Uncommitted user work')
    _, a = await start(engine, 'A', workspace=str(repo))
    _, b = await start(engine, 'B', workspace=str(repo))
    assert a.cwd != b.cwd and a.cwd != str(repo)
    assert (repo / 'local.txt').read_text() == 'Uncommitted user work'


async def test_restart_recovers_active_work_without_replaying_actions(engine):
    t, worker = await start(engine)
    await worker.acknowledge()
    await worker.report(summary='Checkpoint artifact')
    await engine.tasks.close()
    assert engine.tasks.get(t['id'])['state'] == 'interrupted'
    engine.tasks = TaskManager(engine)
    # Reproduce crash rows for controls that were recorded before shutdown.
    engine.tasks.closing = True
    stopped = engine.tasks.create('Stopped', 'Work')
    engine.tasks._update(stopped['id'], state='stopping', revision=2)
    engine.tasks.closing = False
    engine.tasks.start()
    assert engine.tasks.get(t['id'])['session_id'] == worker.session_id
    assert engine.tasks.get(t['id'])['summary'] == 'Checkpoint artifact'
    assert engine.tasks.get(stopped['id'])['state'] == 'stopped'
    assert len(engine.brain.workers) == 1
    engine.tasks.control(t['id'], 'resume')
    await eventually(lambda: len(engine.brain.workers) == 2)
    assert engine.brain.workers[-1].session_id == worker.session_id


async def test_task_http_controls_require_auth_and_current_revision(engine, tmp_path, aiohttp_client):
    from web import WebApp
    app = WebApp(engine, tmp_path)
    client = await aiohttp_client(app.build())
    t = engine.tasks.create('A', 'Work')
    url = f"/api/tasks/{t['id']}/control"
    assert (await client.get('/api/tasks')).status == 401
    assert (await client.post(url, json={'action': 'stop', 'expected_revision': 1})).status == 401
    await client.get(f'/login?token={app.token}', allow_redirects=False)
    assert (await client.post(url, json={'action': 'stop'})).status == 400
    assert (await client.post(url, json={'action': 'steer', 'instruction': 'New plan', 'expected_revision': 1})).status == 200
    assert (await client.post(url, json={'action': 'stop', 'expected_revision': 1})).status == 409
    assert (await client.get('/api/tasks/missing')).status == 404
    details = await (await client.get(f"/api/tasks/{t['id']}")).json()
    assert details['revision'] == 2 and details['commands'][0]['instruction'] == 'New plan'
    today = await (await client.get('/api/today')).json()
    assert today['tasks'][0]['id'] == t['id']


async def test_human_interrupts_automatic_update_without_losing_notice(engine):
    session = engine._chat_session('home')
    entered, release = asyncio.Event(), asyncio.Event()
    prompts = []
    async def ask(prompt, on_text, *args):
        prompts.append(prompt)
        session.busy = True
        try:
            if len(prompts) == 1:
                entered.set()
                await release.wait()
            return 'reply'
        finally:
            session.busy = False
    async def interrupt():
        release.set()
    session.ask, session.interrupt = ask, interrupt
    notice = asyncio.create_task(engine.send_message('home', '', log_user=False, task_notice=True))
    await entered.wait()
    assert await engine.send_message('home', 'Actually change the plan')
    assert await notice is False
    assert prompts[-1].endswith('Actually change the plan')
    assert engine.foreground_waiters == 0


async def test_notification_returns_results_through_main_conversation(engine):
    engine.tasks.start()
    t, worker = await start(engine)
    await worker.acknowledge()
    await worker.report('waiting', 'Which folder should I use?')
    worker.release.set()
    await eventually(lambda: bool(engine.messages('home')) and engine.tasks.get(t['id'])['notice'] == 0)
    assert engine.messages('home')[-1]['task_notice'] is True
    assert 'Which folder should I use?' in engine.brain.asks[-1]['prompt']
    assert engine.tasks.get(t['id'])['notice'] == 0


async def test_open_window_does_not_hold_engine_shutdown(engine, tmp_path, aiohttp_client):
    from web import WebApp
    app = WebApp(engine, tmp_path)
    client = await aiohttp_client(app.build())
    ws = await client.ws_connect('/api/events', headers={'Authorization': 'Bearer ' + app.token})
    assert (await ws.receive_json())['type'] == 'hello'
    async with asyncio.timeout(2):
        await client.server.close()
    assert not app.websockets
