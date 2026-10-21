"""Durable work behind Ken's single conversation.

A task owns its identity, instructions and history; an attempt owns one model
session. User controls fence old attempts immediately, then drain/close them
before a replacement can start. SQLite is the source of truth, not the model.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import logging
from pathlib import Path
import time
import uuid

log = logging.getLogger('ken')
ACTIVE = {'queued', 'running', 'reviewing', 'steering', 'stopping', 'pausing'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL, criteria TEXT NOT NULL,
 project TEXT NOT NULL, workspace TEXT NOT NULL, resources TEXT NOT NULL, state TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, applied_revision INTEGER NOT NULL DEFAULT 0,
 attempt INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT 'work',
 created REAL NOT NULL, updated REAL NOT NULL, activity TEXT NOT NULL DEFAULT '',
 summary TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
 session_id TEXT, request_key TEXT UNIQUE, notice INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS task_attempts (
 task TEXT NOT NULL, number INTEGER NOT NULL, revision INTEGER NOT NULL, mode TEXT NOT NULL,
 state TEXT NOT NULL, started REAL NOT NULL, finished REAL, session_id TEXT,
 summary TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '', PRIMARY KEY(task, number)
);
CREATE TABLE IF NOT EXISTS task_commands (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, revision INTEGER NOT NULL,
 action TEXT NOT NULL, instruction TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL
);
"""

SUPERVISOR = """
YOUR ROLE IN THIS CONVERSATION: You are main Ken, always available to the human.
Keep your turns short. Delegate research, coding, browsing, setup, scheduled work,
and substantial verification to the tasks tools, then return to the conversation.
You have local file tools for your memory and configuration, and task-management
tools; workers have the connected apps and execution tools. Never wait or poll
for a worker. Ask a worker for missing information through task_control; its
answer will come back here. All human interaction stays here, never send them to
another agent chat. Create separate tasks for independently controllable goals.
Give each task a clear objective, relevant context, file paths, and concrete
success criteria. Request resources=['browser'] for browser work, or named shared
resources for anything that must not overlap. For code changes supply workspace
with the repository path; Ken gives that task a worktree from its committed HEAD.
Uncommitted source edits are not copied; include their context explicitly when
needed. Finished changes stay in the task worktree until integration is requested.
Preserve existing
authorizations and include their constraints. Do not grant new authority yourself.
On every turn you receive fresh task state. Use task_list/task_details for older
or more detailed information. Resolve references by the conversation and task
identities; ask when ambiguous. task_control returns a REQUEST, not proof a worker
has applied it: compare revision and applied_revision. Never say stopped until
state is stopped. Stop leaves partial files; it does not undo completed actions.
A restart creates another attempt under the same task. Resume continues its saved
session; retry starts a fresh session with prior results and instructions.
A result in needs_review is unverified: review it with task_control(action='review')
before claiming success. A failed verification needs follow-up work, not a success
announcement. Report useful milestones, questions, failures and verified results
in your own voice, with evidence. Avoid narrating routine tool calls.
"""


class TaskManager:
    def __init__(self, engine, slots=4):
        self.engine, self.db = engine, engine.db
        self.db.executescript(SCHEMA)
        self.root = engine.home / 'tasks'
        self.slots = asyncio.Semaphore(slots)
        self.drivers = {}
        self.sessions = {}
        self.locks = {}
        self.controls = set()
        self.interrupting = set()
        self.cleaning = set()
        self.wake = asyncio.Event()
        self.notifier = None
        self.closing = False
        self.origin = None

    def start(self):
        # A recorded stop must never become work again after a crash. In-flight
        # operations are uncertain, so recover as interrupted until resumed.
        for t in self.list(limit=None):
            if t['state'] == 'stopping':
                self._update(t['id'], state='stopped', applied_revision=t['revision'])
                self._ack(t['id'], t['revision'])
            elif t['state'] == 'pausing':
                self._update(t['id'], state='paused', applied_revision=t['revision'])
                self._ack(t['id'], t['revision'])
            elif t['state'] in {'running', 'reviewing', 'steering'}:
                self._update(t['id'], state='interrupted', activity='Ken restarted. Partial work is saved; ask to resume.', notice=1)
            if t['state'] in ACTIVE:
                self.db.execute("UPDATE task_attempts SET state='interrupted', finished=? WHERE task=? AND finished IS NULL", (time.time(), t['id']))
            if t['state'] == 'queued':
                self._schedule(t['id'])
        self.notifier = asyncio.create_task(self._notifications())
        self.wake.set()

    async def close(self):
        self.closing = True
        if self.notifier:
            self.notifier.cancel()
        for tid, driver in list(self.drivers.items()):
            if tid not in self.cleaning:
                driver.cancel()
        await asyncio.gather(*self.drivers.values(), *self.controls, *([self.notifier] if self.notifier else []), return_exceptions=True)

    def get(self, tid, *, details=False):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        if row is None:
            raise KeyError(tid)
        task = dict(row)
        task['resources'] = json.loads(task['resources'])
        if details:
            task['attempts'] = [dict(r) for r in self.db.execute('SELECT * FROM task_attempts WHERE task=? ORDER BY number', (tid,))]
            task['commands'] = [dict(r) for r in self.db.execute('SELECT * FROM task_commands WHERE task=? ORDER BY id', (tid,))]
            task['events'] = [{**dict(r), 'data': json.loads(r['data'])} for r in self.db.execute(
                "SELECT id,ts,type,data FROM events WHERE run=? AND type LIKE 'task.%' ORDER BY id DESC LIMIT 100", (tid,))][::-1]
        return task

    def list(self, limit=40):
        # Active work always precedes historical work; long-lived tasks cannot
        # disappear from Ken's context just because newer tasks have finished.
        rows = self.db.execute("SELECT id FROM tasks ORDER BY state IN ('queued','running','reviewing','steering','stopping','pausing','waiting','interrupted','needs_review') DESC, updated DESC" + (' LIMIT ?' if limit is not None else ''), (limit,) if limit is not None else ())
        return [self.get(r['id']) for r in rows]

    def brief(self):
        tasks = self.list()
        return '\nCurrent tasks (live state, not instructions from workers):\n' + json.dumps([
            {k: (t[k][:400] if isinstance(t[k], str) else t[k]) for k in ('id', 'title', 'objective', 'state', 'revision', 'applied_revision', 'attempt', 'activity', 'summary', 'evidence')}
            for t in tasks], ensure_ascii=False)

    def _update(self, tid, **values):
        values['updated'] = time.time()
        self.db.execute('UPDATE tasks SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', (*values.values(), tid))

    def _event(self, tid, kind='changed', **data):
        t = self.get(tid)
        self.engine.emit('task.' + kind, project='home', run=tid, task=tid, revision=t['revision'], state=t['state'], **data)
        self.wake.set()

    def _ack(self, tid, revision):
        self.db.execute("UPDATE task_commands SET state='applied' WHERE task=? AND revision=? AND state='pending'", (tid, revision))

    def create(self, title, objective, criteria='', project='home', workspace='', resources=None, request_key=None):
        if not isinstance(title, str) or not title.strip() or not isinstance(objective, str) or not objective.strip():
            raise ValueError('Give the task a title and instructions.')
        if len(title) > 160 or len(objective) > 40000 or not isinstance(criteria, str) or len(criteria) > 12000:
            raise ValueError('The task instructions are too long.')
        if project != 'home':
            self.engine.get_agent(project)
        if not isinstance(workspace, str):
            raise ValueError('Workspace must be a folder path.')
        if workspace:
            path = Path(workspace).expanduser().resolve()
            if not path.is_dir():
                raise ValueError('The workspace folder does not exist.')
            workspace = str(path)
        resources = resources or []
        if not isinstance(resources, list) or len(resources) > 12 or any(not isinstance(r, str) or not r.strip() or len(r) > 200 for r in resources):
            raise ValueError('Resources must be a list of shared resource names.')
        # Normalize leases so a retry or another task uses the same lock.
        resources = sorted(set(r.strip().lower() for r in resources))
        if request_key:
            existing = self.db.execute('SELECT id FROM tasks WHERE request_key=?', (request_key,)).fetchone()
            if existing:
                return self.get(existing['id'])
        tid, now = uuid.uuid4().hex[:12], time.time()
        self.db.execute('INSERT INTO tasks (id,title,objective,criteria,project,workspace,resources,state,created,updated,request_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (tid, title.strip(), objective.strip(), criteria, project, workspace, json.dumps(resources), 'queued', now, now, request_key))
        self._event(tid, 'created')
        self._schedule(tid)
        return self.get(tid)

    def control(self, tid, action, instruction='', expected_revision=None):
        t = self.get(tid)
        if expected_revision is not None and expected_revision != t['revision']:
            raise ValueError('This task changed. Refresh its status before controlling it.')
        if not isinstance(action, str) or action not in {'steer', 'stop', 'pause', 'resume', 'retry', 'review'}:
            raise ValueError('Choose steer, stop, pause, resume, retry or review.')
        if not isinstance(instruction, str) or len(instruction) > 16000:
            raise ValueError('Invalid task instructions.')
        if action == 'steer' and not instruction.strip():
            raise ValueError('Say what should change.')
        if action in {'resume', 'retry', 'review'} and t['state'] in ACTIVE:
            raise ValueError('This task is still active. Steer or stop it first.')
        if action in {'stop', 'pause'} and t['state'] not in ACTIVE:
            return t
        revision = t['revision'] + 1
        driver = self.drivers.get(tid)
        live = driver is not None and not driver.done()
        state = ('stopping' if live else 'stopped') if action == 'stop' else ('pausing' if live else 'paused') if action == 'pause' else ('steering' if live and tid in self.sessions else 'queued')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE task_commands SET state='superseded' WHERE task=? AND state='pending'", (tid,))
            self.db.execute('INSERT INTO task_commands(task,revision,action,instruction,state,created) VALUES(?,?,?,?,?,?)', (tid, revision, action, instruction.strip(), 'pending', time.time()))
            changes = dict(revision=revision, state=state, mode='review' if action == 'review' else 'work' if action in {'steer', 'retry'} else t['mode'], activity={'steering': 'Changing direction…', 'stopping': 'Stopping…', 'pausing': 'Pausing…'}.get(state, 'Waiting to start' if state == 'queued' else ''), notice=0)
            if action in {'retry', 'review'}:
                changes['session_id'] = None
            if not live and action in {'stop', 'pause'}:
                changes['applied_revision'] = revision
                self._ack(tid, revision)
            self._update(tid, **changes)
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        self._event(tid, 'control', action=action)
        if live and driver not in self.interrupting and (action in {'stop', 'pause'} or tid in self.sessions):
            # Capture the old driver/session. A delayed control must never kill
            # a replacement attempt that has already adopted newer instructions.
            self.interrupting.add(driver)
            c = asyncio.create_task(self._interrupt(tid, driver, self.sessions.get(tid)))
            c.add_done_callback(lambda _: self.interrupting.discard(driver))
            self.controls.add(c)
            c.add_done_callback(self.controls.discard)
        elif state == 'queued':
            self._schedule(tid)
        return self.get(tid)

    async def _interrupt(self, tid, driver, session):
        if driver.done():
            return
        if session is not None:
            try:
                await asyncio.wait_for(session.interrupt(), 3)
                await asyncio.wait_for(asyncio.shield(driver), 5)
                return
            except (Exception, asyncio.CancelledError):
                pass
        if tid not in self.cleaning:
            driver.cancel()
        await asyncio.gather(driver, return_exceptions=True)

    def _schedule(self, tid):
        if self.closing or (tid in self.drivers and not self.drivers[tid].done()):
            return
        driver = asyncio.create_task(self._drive(tid))
        self.drivers[tid] = driver
        def finished(f):
            if self.drivers.get(tid) is not f:
                return
            self.drivers.pop(tid, None)
            # Cancellation before the coroutine's first instruction has no
            # finally block; settle its pending control here as well.
            self._settle(tid)
            if not f.cancelled() and f.exception():
                log.error('task driver failed', exc_info=f.exception())
            if self.get(tid)['state'] == 'queued':
                self._schedule(tid)
        driver.add_done_callback(finished)

    def _settle(self, tid):
        t = self.get(tid)
        state = {'steering': 'queued', 'stopping': 'stopped', 'pausing': 'paused'}.get(t['state'])
        if state:
            self._update(tid, state=state, activity='', **({'applied_revision': t['revision']} if state != 'queued' else {}))
            if state != 'queued':
                self._ack(tid, t['revision'])
            self._event(tid)

    async def _git(self, *args):
        process = await asyncio.create_subprocess_exec('git', *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), 30)
        except BaseException:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            raise
        if process.returncode:
            raise ValueError(err.decode(errors='replace').strip()[:800] or 'Git could not prepare the task workspace.')
        return out.decode().strip()

    async def _workspace(self, task):
        folder = self.root / task['id']
        folder.mkdir(parents=True, exist_ok=True)
        if not task['workspace']:
            (folder / 'work').mkdir(exist_ok=True)
            return folder / 'work', []
        source = Path(task['workspace'])
        if not source.is_dir():
            raise ValueError('The task workspace is no longer available.')
        try:
            repo = await self._git('-C', str(source), 'rev-parse', '--show-toplevel')
        except ValueError:
            return source, ['folder:' + str(source)]
        code = folder / 'code'
        async with self.locks.setdefault('git:' + repo, asyncio.Lock()):
            if not (code / '.git').exists():
                await self._git('-C', repo, 'worktree', 'add', '--detach', str(code), 'HEAD')
        return code, []

    def _checkpoint(self, tid, revision, number, session_id):
        self.db.execute('UPDATE task_attempts SET session_id=? WHERE task=? AND number=?', (session_id, tid, number))
        t = self.get(tid)
        if t['revision'] == revision:
            self._update(tid, session_id=session_id)
        else:
            # Steering resumes the interrupted session; retry deliberately does not.
            last = self.db.execute('SELECT action FROM task_commands WHERE task=? ORDER BY id DESC LIMIT 1', (tid,)).fetchone()
            if last and last['action'] in {'steer', 'pause', 'stop'}:
                self._update(tid, session_id=session_id)

    async def _drive(self, tid):
        from engine import until_idle
        session, number, revision = None, None, None
        report = {}
        final_state = 'interrupted'
        stack = AsyncExitStack()
        try:
            initial = self.get(tid)
            # Resource waiters do not consume a worker slot.
            cwd, extra = await self._workspace(initial)
            for resource in sorted(set(initial['resources'] + extra)):
                await stack.enter_async_context(self.locks.setdefault(resource, asyncio.Lock()))
            await stack.enter_async_context(self.slots)
            task = self.get(tid)
            if task['state'] not in {'queued', 'steering'}:
                return
            revision, number = task['revision'], task['attempt'] + 1
            self._update(tid, attempt=number, state='reviewing' if task['mode'] == 'review' else 'running', activity='Starting a review' if task['mode'] == 'review' else 'Starting')
            self.db.execute('INSERT INTO task_attempts(task,number,revision,mode,state,started) VALUES(?,?,?,?,?,?)', (tid, number, revision, task['mode'], 'running', time.time()))
            self._event(tid, 'started', attempt=number)
            from task_tools import worker_tools
            def mcp():
                connected = dict(self.engine.agent_tools(task['project']))
                if 'browser' not in task['resources']:
                    connected.pop('browser', None)
                return {**connected, **worker_tools(self, tid, revision, number, report)}
            session = self.engine.brain.session(cwd=str(cwd), system=lambda: self._worker_prompt(task, cwd), resume=task['session_id'],
                model_fn=(lambda: self.engine.get_agent(task['project']).get('model') or self.engine.brain.model_fn()) if task['project'] != 'home' else None,
                mcp_fn=mcp, env_fn=lambda: {**self.engine._key_values(task['project']), 'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1', 'BASH_MAX_TIMEOUT_MS': '3600000'})
            session.on_session_id = lambda sid: self._checkpoint(tid, revision, number, sid)
            self.sessions[tid] = session
            async def text(value):
                value = self.engine.mask(task['project'], value)
                self.engine.emit('task.text', project='home', run=tid, task=tid, attempt=number, revision=revision, text=value)
                if self.get(tid)['revision'] == revision:
                    self._update(tid, activity=value.strip().split('\n')[0][:180])
            async def tool(value):
                if self.get(tid)['revision'] == revision:
                    self._update(tid, activity=self.engine.mask(task['project'], value)[:180])
                    self._event(tid, 'progress')
            prompt = self._instructions(task)
            # A model turn ending is not a task finishing. In particular an SDK
            # background notification may end a turn without any final report.
            # Keep the same session and give incomplete turns a bounded nudge.
            final = None
            for _ in range(3):
                final = await until_idle(session, session.ask(prompt, text, tool))
                if report or self.get(tid)['revision'] != revision:
                    break
                prompt = ('This task is still active and you have not called report_task. '
                          'Continue the work in this session, wait for any commands to finish, and report the evidence. '
                          'Do not repeat completed actions. If blocked, report waiting with the exact question. '
                          'Current instructions: ' + self._instructions(task))
            if session.session_id:
                self._checkpoint(tid, revision, number, session.session_id)
            current = self.get(tid)
            if current['revision'] != revision:
                final_state = 'superseded'
                return
            summary = report.get('summary') or final or 'The worker returned without a result.'
            evidence = report.get('evidence', '')
            status = report.get('status', 'unverified')
            state = {'waiting': 'waiting', 'failed': 'failed', 'changes_needed': 'needs_review', 'passed': 'done'}.get(status, 'needs_review')
            if state == 'done' and (task['mode'] != 'review' or not evidence.strip()):
                state = 'needs_review'
            final_state = state
            self._update(tid, state=state, summary=self.engine.mask(task['project'], summary)[:12000], evidence=self.engine.mask(task['project'], evidence)[:12000], activity='', notice=1)
            self.db.execute('UPDATE task_attempts SET summary=?,evidence=? WHERE task=? AND number=?', (self.get(tid)['summary'], self.get(tid)['evidence'], tid, number))
            self._event(tid, 'finished')
        except asyncio.CancelledError:
            t = self.get(tid)
            if t['state'] in {'running', 'reviewing', 'queued'}:
                self._update(tid, state='interrupted', activity='Execution interrupted. Partial work is saved.', notice=1)
            raise
        except Exception as exc:
            log.warning('task %s failed: %s', tid, exc)
            t = self.get(tid)
            if revision is None or t['revision'] == revision:
                self._update(tid, state='failed', activity='', summary=self.engine.mask(t['project'], str(exc))[:2000], notice=1)
                final_state = 'failed'
                self._event(tid, 'finished')
        finally:
            self.cleaning.add(tid)
            if session is not None:
                if session.session_id and number is not None:
                    self._checkpoint(tid, revision, number, session.session_id)
                try:
                    # The SDK drains, terminates and reaps its process here.
                    # Cancelling that teardown early can orphan a live tool.
                    await session.close()
                except (Exception, asyncio.CancelledError):
                    pass
            # Hold resource leases through teardown, including interrupted tools.
            await stack.aclose()
            self.sessions.pop(tid, None)
            self.cleaning.discard(tid)
            if number is not None:
                self.db.execute('UPDATE task_attempts SET state=?,finished=? WHERE task=? AND number=?', (final_state, time.time(), tid, number))
            self._settle(tid)
            self.wake.set()

    def _instructions(self, task):
        commands = [dict(r) for r in self.db.execute('SELECT revision,action,instruction FROM task_commands WHERE task=? ORDER BY id', (task['id'],))]
        return json.dumps({'task': task['id'], 'revision': task['revision'], 'mode': task['mode'], 'objective': task['objective'], 'success_criteria': task['criteria'], 'changes': commands, 'previous_result': task['summary'], 'previous_evidence': task['evidence']}, ensure_ascii=False)

    def _worker_prompt(self, task, cwd):
        base = self.engine.system(task['project']) if task['project'] != 'home' else self.engine.home_system()
        return base + f"""\n\nYou are a background worker managed by main Ken. Work only on task {task['id']}.
Your task workspace is {cwd}. This is an execution attempt, not a conversation
with the human. Call task_checkpoint first to acknowledge the current revision.
Ken owns task execution: run tools in the foreground, use appropriate command
timeouts (up to one hour), and do not launch detached shell jobs or background subagents. Keep
working until you report a result or a blocker; a conversational sign-off is not
completion. Do not leave processes running when you report a final result.
Use it again at meaningful milestones, with a concise progress summary. If it
reports a stale revision, stop immediately. Latest task instructions override
older instructions in this session. Preserve partial work and never repeat an
external action just because the session resumed: first verify what happened.
If you need input or approval, report_task(status='waiting') with the exact
question and stop. Main Ken will ask the human. Do not message the human yourself.
Use only browser tabs created for this task. Browser access requires the browser
resource lease; if unavailable, report waiting instead of driving a shared browser.
Finish with report_task: summary, evidence (tests, paths, links, limitations), and
status 'ready', 'waiting', or 'failed'. Then end your turn immediately.
This attempt's mode is {task['mode']}. In review mode inspect the existing work and
run appropriate checks without changing it or repeating external actions; report
'passed' only with concrete evidence that meets the success criteria, otherwise
'changes_needed' with the gaps. A worker's earlier claim is not verification.
"""

    async def _notifications(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            await asyncio.sleep(.5)  # coalesce simultaneous completions
            pending = [(t['id'], t['revision'], t['state']) for t in self.list(limit=None) if t['notice']]
            if not pending:
                continue
            if self.engine.chat_busy('home') or self.engine.foreground_waiters:
                await asyncio.sleep(1)
                self.wake.set()
                continue
            ok = await self.engine.send_message('home', '', log_user=False, task_notice=True,
                prompt='(Background task updates arrived. Use the fresh task snapshot. Relay questions or meaningful results in the main chat; launch review for unverified work. Do not wait on workers. Do not claim success without verification.)')
            if ok:
                for tid, rev, state in pending:
                    self.db.execute('UPDATE tasks SET notice=0 WHERE id=? AND revision=? AND state=?', (tid, rev, state))
            else:
                await asyncio.sleep(3)
                self.wake.set()

    def request_key(self, title, objective):
        return hashlib.sha256(f'{self.origin}:{title}:{objective}'.encode()).hexdigest() if self.origin else None
