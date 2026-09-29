"""Desktop settings, scheduling and permission boundaries; no live model calls."""
import asyncio
import json
import time
from datetime import datetime

import pytest

from test_engine import engine, settle
from desktop.services import DesktopServices
from web import WebApp


def test_settings_validate_and_preserve_other_jobs(engine):
    services = DesktopServices(engine)
    (engine.home / 'jobs.json').write_text(json.dumps([{'name': 'nightly-review', 'time': '21:00', 'prompt': 'remember'}]))
    result = services.save_settings({'name': 'Alex', 'model': 'sonnet', 'brief_time': '08:30', 'brief_enabled': False})
    assert result['name'] == 'Alex'
    assert not result['brief_enabled']
    assert services.jobs()[0]['name'] == 'nightly-review'
    assert services.jobs()[1]['time'] == '08:30'
    for bad in ({'brief_time': '25:00'}, {'model': 'invented-model'}, {'character': 'yes'}, {'name': 42}):
        with pytest.raises(ValueError):
            services.save_settings(bad)
    assert services.settings()['name'] == 'Alex'


async def test_permission_waits_for_user_and_cleans_up(engine):
    services = DesktopServices(engine)
    request = asyncio.create_task(services.can_use_tool('Bash', {'command': 'echo hello'}, None))
    await asyncio.sleep(0)
    pending = services.permission_list()
    assert len(pending) == 1 and 'future' not in pending[0]
    assert not request.done()
    services.resolve_permission(pending[0]['id'], False)
    assert (await request).behavior == 'deny'
    assert services.permission_list() == []
    request = asyncio.create_task(services.can_use_tool('Write', {'file_path': 'draft.md'}, None))
    await asyncio.sleep(0)
    services.resolve_permission(services.permission_list()[0]['id'], True)
    assert (await request).updated_input == {'file_path': 'draft.md'}


async def test_browser_requires_connected_client_and_correlates_results(engine):
    services = DesktopServices(engine)
    with pytest.raises(ValueError, match='not connected'):
        await services.browser_action({'action': 'read'})
    services.browser_seen = time.monotonic()
    task = asyncio.create_task(services.browser_action({'action': 'read'}))
    item = await services.browser_queue.get()
    services.browser_waiters[item['id']].set_result({'text': 'page'})
    assert await task == {'text': 'page'}
    assert services.browser_waiters == {}
    servers = services.mcp_servers()
    assert 'ken' in servers


async def test_brief_catches_up_once_per_day_and_respects_pause(engine):
    services = DesktopServices(engine)
    services.save_settings({'brief_time': '07:00', 'brief_enabled': True, 'onboarded': True})
    await services.schedule_tick(datetime(2026, 9, 25, 8, 0))
    await services.brief_task
    assert services.brief()['text'] == 'did it'
    count = len(engine.brain.asks)
    await services.schedule_tick(datetime(2026, 9, 25, 9, 0))
    assert len(engine.brain.asks) == count
    services.save_settings({'brief_enabled': False})
    await services.schedule_tick(datetime(2026, 9, 26, 8, 0))
    assert len(engine.brain.asks) == count


async def test_desktop_endpoints_auth_and_safe_previews(engine, aiohttp_client):
    services = DesktopServices(engine)
    app = WebApp(engine, engine.home, services=services)
    client = await aiohttp_client(app.build())
    assert (await client.put('/api/settings', json={'name': 'Intruder'})).status == 401
    await client.get('/login?token=' + app.token, allow_redirects=False)
    assert (await client.put('/api/settings', json={'brief_time': 'bad'})).status == 400
    assert (await client.put('/api/settings', json={'name': 'Alex'})).status == 200
    meta = await (await client.get('/api/meta')).json()
    assert meta['desktop'] and meta['settings']['name'] == 'Alex'
    dest = engine.new_file_path('home', 'draft.html')
    dest.write_text('<script>alert(1)</script>')
    response = await client.get('/api/chats/home/files/draft.html')
    assert response.headers['Content-Disposition'].startswith('attachment')
    assert response.headers['Content-Security-Policy'].startswith('sandbox')
    preview = await (await client.get('/api/chats/home/preview/draft.html')).json()
    assert preview['text'] == '<script>alert(1)</script>'
    files = await (await client.get('/api/library')).json()
    assert files[0]['project'] == 'home'
    assert (await client.get('/api/chats/home/preview/..%2Fweb-token')).status == 404


def test_chat_preferences_preserve_custom_jobs_and_validate_atomically(engine):
    services = DesktopServices(engine)
    original = [
        {'name': 'morning-brief', 'time': '07:00', 'days': 'weekdays', 'prompt': 'My custom brief'},
        {'name': 'nightly-review', 'time': '21:00', 'prompt': 'My custom memory review'},
        {'name': 'other-job', 'time': '12:00', 'prompt': 'Leave this alone'},
    ]
    (engine.home / 'jobs.json').write_text(json.dumps(original))
    result = services.preferences('update', {'brief_time': '08:30', 'brief_enabled': True, 'brief_topics': 'Product launches and commitments',
                                              'memory_time': '22:00', 'memory_enabled': True})
    assert result['settings']['onboarded']
    assert result['settings']['memory_time'] == '22:00'
    jobs = services.jobs()
    assert len(jobs) == 3
    assert jobs[0]['prompt'] == 'My custom brief' and jobs[0]['days'] == 'weekdays'
    assert jobs[1]['name'] == 'nightly-review' and jobs[1]['prompt'] == 'My custom memory review'
    assert jobs[2] == original[2]
    before = (engine.home / 'jobs.json').read_text()
    for changes in ({'brief_time': '06:00', 'memory_time': '25:00'}, {'memory_enabled': 'yes'}, {'unknown': True}):
        with pytest.raises(ValueError):
            services.preferences('update', changes)
        assert (engine.home / 'jobs.json').read_text() == before
    assert services.preferences('read')['settings']['brief_time'] == '08:30'
    services.preferences('update', {'memory_enabled': False})
    assert not services.settings()['memory_enabled']
    assert services.settings()['brief_enabled']
    assert len([j for j in services.jobs() if j['name'] == 'nightly-review']) == 1


async def test_chat_setup_runs_only_chosen_routines_and_keeps_brief_topics(engine):
    from desktop.runtime import initialize
    initialize(engine.home)
    services = DesktopServices(engine)
    await services.schedule_tick(datetime(2026, 9, 25, 23, 0))
    assert not engine.brain.asks
    services.preferences('update', {'brief_time': '08:00', 'brief_enabled': True, 'brief_topics': 'My launch and follow-ups'})
    assert not services.settings()['memory_configured']
    await services.schedule_tick(datetime(2026, 9, 25, 23, 0))
    await services.brief_task
    assert 'My launch and follow-ups' in engine.brain.asks[-1]['prompt']
    await services.schedule_tick(datetime(2026, 9, 25, 23, 1))
    assert len(engine.brain.asks) == 1
    services.preferences('update', {'memory_time': '21:30', 'memory_enabled': True})
    await services.schedule_tick(datetime(2026, 9, 25, 23, 2))
    await services.memory_task
    prompt = engine.brain.asks[-1]['prompt']
    assert str(engine.home / 'history') in prompt
    assert '~/.ken' not in prompt
    assert 'daily memory check' in prompt
    assert engine.kv_get('desktop:memory_review')
    await services.schedule_tick(datetime(2026, 9, 25, 23, 3))
    assert len(engine.brain.asks) == 2
    services.preferences('update', {'memory_enabled': False, 'brief_enabled': False})
    await services.schedule_tick(datetime(2026, 9, 26, 23, 0))
    assert len(engine.brain.asks) == 2
    # Persisted schedules and the pause survive a new service instance.
    assert not DesktopServices(engine).settings()['memory_enabled']


def test_legacy_onboarding_keeps_existing_schedules(engine):
    services = DesktopServices(engine)
    (engine.home / 'jobs.json').write_text(json.dumps([{'name': 'nightly-review', 'time': '20:00', 'prompt': 'Review'}]))
    engine.kv_set('desktop:settings', json.dumps({'onboarded': True}))
    assert services.settings()['memory_configured']
    assert services.settings()['memory_enabled']
    assert services.settings()['memory_time'] == '20:00'


def test_projects_from_chat_keep_existing_folder_and_return_context(engine, tmp_path):
    services = DesktopServices(engine)
    existing = tmp_path / 'existing-work'
    existing.mkdir()
    document = existing / 'notes.md'
    document.write_text('Existing work stays here.')
    project = services.projects({'action': 'create', 'name': 'Acme', 'path': str(existing),
                                 'context': 'A small agency website.'})
    assert project['workdir'] == str(existing)
    assert document.read_text() == 'Existing work stays here.'
    from pathlib import Path
    assert 'A small agency website.' in Path(project['context_file']).read_text()
    assert services.projects({'action': 'list'})[0]['id'] == project['id']
    for request in ({'action': 'create', 'name': 'Missing', 'path': str(tmp_path / 'missing')},
                    {'action': 'create', 'name': ''}, {'action': 'create', 'name': 123},
                    {'action': 'delete', 'name': 'Acme'}):
        with pytest.raises(ValueError):
            services.projects(request)
    assert len(services.projects({'action': 'list'})) == 1


async def test_clear_thread_archives_messages_but_keeps_memory_and_files(engine):
    from pathlib import Path
    memory = engine.home / 'memory'
    memory.mkdir(exist_ok=True)
    (memory / 'work.md').write_text('Runs a small agency.')
    artifact = engine.new_file_path('home', 'draft.txt')
    artifact.write_text('Useful work')
    await engine.send_message('home', 'Remember this')
    old = engine.messages('home')
    await engine.reset_chat('home', clear=True)
    assert engine.chat_view('home')['messages'] == []
    assert engine.chat_view('home')['cleared_at'] > 0
    assert engine.messages('home') == old
    assert (memory / 'work.md').read_text() == 'Runs a small agency.'
    assert artifact.read_text() == 'Useful work'
    assert engine.kv_get('session:home') is None
    await engine.send_message('home', 'A fresh task')
    assert [m['text'] for m in engine.chat_view('home')['messages']] == ['A fresh task', 'did it']
    lock = engine.chat_locks['home']
    async with lock:
        with pytest.raises(ValueError, match='still working'):
            await engine.reset_chat('home', clear=True)
    assert len(engine.chat_view('home')['messages']) == 2


async def test_message_acceptance_is_immediate_and_retry_is_idempotent(engine):
    class Request:
        match_info = {'pid': 'home'}
        async def json(self):
            return {'text': 'Do the task', 'files': [], 'client_id': 'request-123'}
    app = WebApp(engine, engine.home)
    response = await app.post_message(Request())
    accepted = json.loads(response.body)['message']
    assert accepted['client_id'] == 'request-123'
    assert not engine.brain.asks  # Acceptance does not wait for a model turn.
    retry = json.loads((await app.post_message(Request())).body)['message']
    assert retry['id'] == accepted['id']
    await asyncio.gather(*app.bg)
    assert len(engine.brain.asks) == 1
    assert len([m for m in engine.messages('home') if m['role'] == 'you']) == 1


async def test_background_warmup_has_no_greeting_and_first_message_reuses_session(engine):
    class Request:
        match_info = {'pid': 'home'}
    app = WebApp(engine, engine.home)
    assert (await app.warm_chat(Request())).status == 200
    assert not engine.brain.asks
    await asyncio.gather(*app.bg)
    session = engine.chats['home']
    assert session.warmed and not engine.chat_busy('home')
    assert not engine.messages('home')
    await engine.send_message('home', 'Help me write an email')
    assert engine.chats['home'] is session
    assert len(engine.brain.asks) == 1
    await engine.reset_chat('home', clear=True)
    await app.warm_chat(Request())
    await asyncio.gather(*app.bg)
    assert engine.chats['home'] is not session
    assert not engine.chat_view('home')['messages']
    assert len(engine.brain.asks) == 1


async def test_voice_message_keeps_transcript_waveform_and_audio(engine):
    body = {'text': 'Please plan my day.', 'files': ['voice-test.webm'], 'client_id': 'voice-123',
            'voice': {'seconds': 2.5, 'peaks': [.1, .8, .3]}}
    class Request:
        match_info = {'pid': 'home'}
        async def json(self):
            return body
    app = WebApp(engine, engine.home)
    response = await app.post_message(Request())
    assert response.status == 200
    await asyncio.gather(*app.bg)
    saved = engine.chat_view('home')['messages'][0]
    assert saved['voice'] == body['voice']
    assert saved['text'] == body['text']
    assert saved['files'] == body['files']
    assert 'Please plan my day.' in engine.brain.asks[-1]['prompt']
    assert 'voice-test.webm' not in engine.brain.asks[-1]['prompt']
    assert 'Attached in' not in engine.brain.asks[-1]['prompt']
    body['client_id'] = 'voice-invalid'
    body['voice']['seconds'] = float('inf')
    assert (await app.post_message(Request())).status == 400


async def test_voice_failure_returns_recoverable_error_and_cleans_temp_audio(engine):
    from pathlib import Path
    paths = []
    def broken(path):
        paths.append(Path(path))
        assert paths[-1].read_bytes() == b'audio'
        raise TimeoutError('Voice transcription took too long. Your recording is kept; try again.')
    class Request:
        async def read(self):
            return b'audio'
    response = await WebApp(engine, engine.home, transcribe=broken).voice(Request())
    assert response.status == 503
    assert 'try again' in json.loads(response.body)['error']
    assert all(not path.exists() for path in paths)


async def test_empty_voice_result_cannot_start_a_model_turn(engine):
    class Recording:
        async def read(self):
            return b'quiet recording'
    class Message:
        match_info = {'pid': 'home'}
        async def json(self):
            return {'text': '   ', 'files': ['voice-quiet.webm'], 'voice': {'seconds': 2, 'peaks': [.01]}}
    app = WebApp(engine, engine.home, transcribe=lambda _: '   ')
    response = await app.voice(Recording())
    assert response.status == 422
    assert 'recording is saved' in json.loads(response.body)['error']
    assert (await app.post_message(Message())).status == 422
    assert not app.bg and not engine.brain.asks
    assert not engine.messages('home')


async def test_temporary_recording_playback_allowed_without_opening_other_origins(engine):
    response = await WebApp(engine, engine.home).index(None)
    policy = response.headers['Content-Security-Policy']
    assert "media-src 'self' blob:;" in policy
    assert "script-src 'self';" in policy
    assert "object-src 'none'" in policy
