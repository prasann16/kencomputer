"""The engine: Ken's conversations — one with Ken, one per project — in one
place that every screen (the app, Telegram) reads from.

A project is a folder, ~/.ken/projects/<id>/: project.json (its name, and the
code folder if it has one), CONTEXT.md (what the project is — Ken reads it
with every message and keeps it current) and files/ (whatever you drop in).
It's the same Ken with the same memory; a project chat just adds that
project's context and works in its folder.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from brains import Brain

log = logging.getLogger("ken")

HOME = "home"  # the conversation with Ken itself
COLORS = ["#1F4E79", "#7A4E8C", "#5E7A3A", "#9C4A1A", "#2E6F73", "#8A3B5C", "#4A4F8C", "#6B5B2E"]
GREET_AFTER = 3 * 3600  # speak first when a chat is opened after this long
WELCOME = "Hey, I’m Ken. Your assistant.\n\nTell me what you do for work and what you’d like off your plate. A voice note is perfect."

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL, project TEXT, run TEXT, type TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_project ON events(project, id);
CREATE INDEX IF NOT EXISTS events_run ON events(run, id);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL, prompt TEXT NOT NULL,
  source TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
  started REAL, finished REAL, activity TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, run TEXT, kind TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, next TEXT NOT NULL, status TEXT NOT NULL,
  created REAL NOT NULL, resolved REAL, final_body TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

DEFAULT_AUTONOMY = {
    "alone": ["Check users & errors", "Draft replies", "Open PRs", "Draft posts"],
    "ask": ["Email customers", "Post publicly", "Merge & ship", "Spend money"],
}
APPROVAL_KINDS = ("email", "post", "merge", "spend", "other")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "project"


def safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f]", "", name).lstrip(".")
    return name[:180]


class Engine:
    def __init__(self, home: Path, brain: Brain) -> None:
        self.home = Path(home)
        self.brain = brain
        self.projects_dir = self.home / "projects"
        self.requests_dir = self.home / "requests"
        self.db = sqlite3.connect(self.home / "app.db", isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.subscribers: set[asyncio.Queue] = set()
        self.chats: dict[str, object] = {}
        self.chat_locks: dict[str, asyncio.Lock] = {}
        self.home_cwd = self.home / "work"
        self.home_system: Callable[[], str] = lambda: ""
        self.project_base: Callable[[], str] = lambda: ""
        self.history_hook: Callable[[str, str], None] | None = None
        self._greeting: set[str] = set()
        self.run_tasks: dict[str, asyncio.Task] = {}
        self.run_sessions: dict[str, object] = {}
        self.run_slots = asyncio.Semaphore(4)
        self._bg: list[asyncio.Task] = []

    def configure_home(self, cwd: Path, system: Callable[[], str], project_system: Callable[[], str] | None = None) -> None:
        self.home_cwd = Path(cwd)
        self.home_system = system
        self.project_base = project_system or system

    def start(self) -> None:
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()  # a restart ends whatever was running; say so instead of leaving ghosts
        self.db.execute(
            "UPDATE runs SET status='stopped', finished=?, summary=COALESCE(summary,'Interrupted by a restart.') "
            "WHERE status IN ('running','queued')", (now,),
        )
        self._bg.append(asyncio.create_task(self._watch_requests()))

    def busy(self) -> bool:
        if any(not t.done() for t in self.run_tasks.values()):
            return True
        return any(lock.locked() for lock in self.chat_locks.values())

    # ------------------------------------------------------------------- events

    def _publish(self, event: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def emit(self, type_: str, project: str | None = None, run: str | None = None, **data) -> dict:
        """A stored event (messages, run steps): screens get it live and on reload."""
        ts = time.time()
        cur = self.db.execute(
            "INSERT INTO events (ts, project, run, type, data) VALUES (?,?,?,?,?)",
            (ts, project, run, type_, json.dumps(data)),
        )
        event = {"id": cur.lastrowid, "ts": ts, "project": project, "run": run, "type": type_, **data}
        self._publish(event)
        return event

    def broadcast(self, type_: str, project: str | None = None, **data) -> None:
        """A live-only event (typing, streaming text): never stored."""
        self._publish({"type": type_, "project": project, "ts": time.time(), **data})

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def messages(self, pid: str, limit: int = 150) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM (SELECT * FROM events WHERE project=? AND type='message' ORDER BY id DESC LIMIT ?) ORDER BY id",
            (pid, limit),
        ).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "project": r["project"], **json.loads(r["data"])} for r in rows]

    def welcome(self, pid: str) -> None:
        """Ken's first words in a brand-new home chat, as a real message that stays in the
        thread. Not passed to history_hook: the harness counts Ken's first real reply as its birth."""
        if pid != HOME or self.db.execute("SELECT 1 FROM events WHERE project=? AND type='message' LIMIT 1", (pid,)).fetchone():
            return
        self.emit("message", project=pid, role="ken", text=WELCOME, via="welcome")

    def log_message(self, pid: str, role: str, text: str, **extra) -> dict:
        if pid == HOME and self.history_hook is not None and text:
            self.history_hook(role, text)  # the daily transcript that nightly memory reads
        return self.emit("message", project=pid, role=role, text=text, **extra)

    # ----------------------------------------------------------------- projects

    def _pdir(self, pid: str) -> Path:
        if pid == HOME or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", pid or ""):
            raise KeyError(pid)
        return self.projects_dir / pid

    def get_project(self, pid: str) -> dict:
        try:
            meta = json.loads((self._pdir(pid) / "project.json").read_text())
        except (OSError, ValueError):
            raise KeyError(pid)
        meta["id"] = pid
        meta["workdir"] = str(self.workdir(meta))
        return meta

    def list_projects(self) -> list[dict]:
        if not self.projects_dir.exists():
            return []
        out = []
        for d in self.projects_dir.iterdir():
            try:
                out.append(self.get_project(d.name))
            except KeyError:
                continue
        return sorted(out, key=lambda p: p.get("created", 0))

    def workdir(self, meta: dict) -> Path:
        if meta.get("path"):
            return Path(os.path.expanduser(meta["path"]))
        return self.projects_dir / meta["id"]

    def create_project(self, name: str, path: str = "", context: str = "", blurb: str = "") -> dict:
        name = name.strip()[:60]
        if not name:
            raise ValueError("a project needs a name")
        path = path.strip()
        if path and not Path(os.path.expanduser(path)).is_dir():
            raise ValueError(f"folder not found: {path}")
        base = slugify(name)
        if base == HOME:
            base = "home-project"
        pid, n = base, 2
        while (self.projects_dir / pid).exists():
            pid, n = f"{base}-{n}", n + 1
        d = self.projects_dir / pid
        (d / "files").mkdir(parents=True)
        meta = {"name": name, "path": path, "blurb": blurb.strip()[:200], "connected": [],
                "color": COLORS[len(self.list_projects()) % len(COLORS)], "created": time.time()}
        (d / "project.json").write_text(json.dumps(meta, indent=1) + "\n")
        (d / "CONTEXT.md").write_text(f"# {name}\n\n{context.strip()}\n")
        self.emit("project.created", project=pid, name=name)
        return self.get_project(pid)

    def get_context(self, pid: str) -> str:
        try:
            return (self._pdir(pid) / "CONTEXT.md").read_text()
        except OSError:
            return ""

    def set_context(self, pid: str, text: str) -> None:
        (self._pdir(pid) / "CONTEXT.md").write_text(text.rstrip() + "\n")
        self.emit("project.updated", project=pid)

    def update_project(self, pid: str, **fields) -> dict:
        meta = self.get_project(pid)
        for k in ("name", "blurb", "path"):
            if fields.get(k) is not None:
                meta[k] = str(fields[k]).strip()
        meta.pop("id", None)
        meta.pop("workdir", None)
        (self._pdir(pid) / "project.json").write_text(json.dumps(meta, indent=1) + "\n")
        self.emit("project.updated", project=pid)
        return self.get_project(pid)

    def get_autonomy(self, pid: str) -> dict:
        try:
            data = json.loads((self._pdir(pid) / "autonomy.json").read_text())
            return {"alone": list(data.get("alone", [])), "ask": list(data.get("ask", []))}
        except (OSError, ValueError):
            return {k: list(v) for k, v in DEFAULT_AUTONOMY.items()}

    def set_autonomy(self, pid: str, alone: list, ask: list) -> None:
        self.get_project(pid)
        clean = lambda xs: [str(x).strip()[:60] for x in xs if str(x).strip()][:30]  # noqa: E731
        (self._pdir(pid) / "autonomy.json").write_text(json.dumps({"alone": clean(alone), "ask": clean(ask)}, indent=1) + "\n")
        self.emit("project.updated", project=pid)

    # -------------------------------------------------------------------- files

    def files_dir(self, pid: str) -> Path:
        return self.home / "inbox" if pid == HOME else self._pdir(pid) / "files"

    def list_files(self, pid: str) -> list[dict]:
        d = self.files_dir(pid)
        if not d.exists():
            return []
        items = [
            {"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
            for f in d.iterdir() if f.is_file() and not f.name.startswith(".")
        ]
        return sorted(items, key=lambda f: -f["mtime"])

    def file_path(self, pid: str, name: str) -> Path:
        clean = safe_filename(name)
        p = self.files_dir(pid) / clean
        if not clean or clean != name or not p.is_file():
            raise KeyError(name)
        return p

    def new_file_path(self, pid: str, name: str) -> Path:
        """Where an upload lands; never overwrites, never escapes the folder."""
        if pid != HOME:
            self.get_project(pid)
        clean = safe_filename(name) or f"file-{time.strftime('%Y%m%d-%H%M%S')}"
        d = self.files_dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        stem, suffix = os.path.splitext(clean)
        p, n = d / clean, 2
        while p.exists():
            p, n = d / f"{stem}-{n}{suffix}", n + 1
        return p

    # ------------------------------------------------------------------ prompts

    def system(self, pid: str) -> str:
        """Ken's own system prompt; a project chat adds that project's context."""
        if pid == HOME:
            return self.home_system()
        base = self.project_base()
        p = self.get_project(pid)
        d = self._pdir(pid)
        auto = self.get_autonomy(pid)
        r = self.requests_dir
        return (
            f"{base}\n\n=== This conversation is about the project {p['name']} ===\n"
            f"You're working in {p['workdir']}. Its context is {d / 'CONTEXT.md'} (below) — keep it "
            f"current as you learn things. Files dropped into this chat are in {d / 'files'}. "
            f"List services connected for this project in the \"connected\" array of {d / 'project.json'}.\n"
            f"Fine to do on your own: {'; '.join(auto['alone']) or 'nothing'}. Ask first: {'; '.join(auto['ask']) or 'nothing'} "
            f"(rules in {d / 'autonomy.json'}). To ask, or to hand long work to the background, write JSON into {r}/: "
            f'{{"type": "approval", "project": "{pid}", "kind": "email|post|merge|spend|other", "title": "...", "body": "exactly what they\'ll see", "next": "what you do once approved"}} or '
            f'{{"type": "run", "project": "{pid}", "title": "...", "prompt": "full instructions"}}.\n\n'
            f"{self.get_context(pid).strip()}"
        )

    def projects_line(self) -> str:
        ps = self.list_projects()
        if not ps:
            return ""
        return "Their projects (each has its own chat and context in " + str(self.projects_dir) + "): " + ", ".join(
            f"{p['name']} ({p['workdir']})" for p in ps
        )

    async def _git(self, workdir: Path) -> str:
        if not (workdir / ".git").exists():
            return ""

        async def git(*args) -> str:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(workdir), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            return out.decode(errors="replace").strip()

        try:
            branch = await git("rev-parse", "--abbrev-ref", "HEAD")
            dirty = len((await git("status", "--porcelain")).splitlines())
            return f"git: {branch}, {dirty} uncommitted, last commit {await git('log', '-1', '--format=%s (%cr)')}"
        except Exception:
            return ""

    async def state(self, pid: str) -> str:
        """A one-line snapshot sent with each message so replies can be specific."""
        parts = [time.strftime("%a %d %b %H:%M")]
        if pid != HOME:
            since = self._last_message_ts(pid)
            new = [f["name"] for f in self.list_files(pid) if f["mtime"] > since]
            if new:
                parts.append("new files: " + ", ".join(new[:10]))
            git = await self._git(self.workdir(self.get_project(pid)))
            if git:
                parts.append(git)
        return "[" + " · ".join(parts) + "]"

    # -------------------------------------------------------------------- chats

    def _chat_session(self, pid: str):
        s = self.chats.get(pid)
        if s is None:
            cwd = self.home_cwd if pid == HOME else self.workdir(self.get_project(pid))
            s = self.brain.session(cwd=str(cwd), system=lambda: self.system(pid), resume=self.kv_get(f"session:{pid}"))
            self.chats[pid] = s
        return s

    def chat_busy(self, pid: str) -> bool:
        lock = self.chat_locks.get(pid)
        return bool(lock and lock.locked())

    def _last_message_ts(self, pid: str) -> float:
        r = self.db.execute(
            "SELECT ts FROM events WHERE project=? AND type='message' ORDER BY id DESC LIMIT 1", (pid,)
        ).fetchone()
        return r["ts"] if r else 0.0

    async def warm(self, pid: str) -> None:
        """Connect before the first message so the reply starts fast."""
        try:
            await self._chat_session(pid).warm()
        except Exception as e:
            log.warning("warm-up for %s failed: %s", pid, e)

    async def send_message(
        self,
        pid: str,
        text: str,
        files: list[str] | None = None,
        *,
        prompt: str | None = None,
        log_user: bool = True,
        via: str | None = None,
        listener: Callable[[str], Awaitable[None]] | None = None,
        quiet: bool = False,
    ) -> None:
        """Talk to Ken (or Ken-on-a-project). Replies stream to every screen;
        `listener` also gets each finished utterance (Telegram uses it)."""
        if pid != HOME:
            self.get_project(pid)
        files = [f for f in (files or []) if f]
        if log_user:
            self.log_message(pid, "you", text, files=files, **({"via": via} if via else {}))
        body = prompt if prompt is not None else text
        if files and prompt is None:
            body += f"\n\n(Attached in {self.files_dir(pid)}: {', '.join(files)})"
        body = f"{await self.state(pid)}\n{body}"
        lock = self.chat_locks.setdefault(pid, asyncio.Lock())
        async with lock:
            self.broadcast("chat.busy", pid, busy=True)
            session = self._chat_session(pid)

            async def on_text(t: str) -> None:
                if t.strip() == "NOTHING_TO_SAY":
                    return
                if not quiet:
                    self.log_message(pid, "ken", t)
                if listener is not None:
                    await listener(t)

            async def on_tool(line: str) -> None:
                self.broadcast("chat.tool", pid, text=line)

            async def on_delta(t: str) -> None:
                if not quiet:
                    self.broadcast("chat.delta", pid, text=t)

            try:
                await asyncio.wait_for(session.ask(body, on_text, on_tool, on_delta), timeout=1800)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("chat %s failed: %s", pid, e)
                await session.close()
                self.chats.pop(pid, None)
                msg = "That took too long, so I stopped." if isinstance(e, asyncio.TimeoutError) else f"Something went wrong: {e}"
                self.log_message(pid, "ken", msg, error=True)
                if listener is not None:
                    await listener(msg)
            finally:
                if session.session_id:
                    self.kv_set(f"session:{pid}", session.session_id)
                self.broadcast("chat.busy", pid, busy=False)

    async def on_open(self, pid: str) -> None:
        """A chat was opened: warm up, and if it's been a while, speak first."""
        if pid != HOME:
            self.get_project(pid)
        await self.warm(pid)
        if self.chat_busy(pid) or pid in self._greeting or time.time() - self._last_message_ts(pid) < GREET_AFTER:
            return
        self._greeting.add(pid)
        try:
            await self.send_message(pid, "", log_user=False, prompt="(They just opened this chat. You speak first.)")
        finally:
            self._greeting.discard(pid)

    async def files_dropped(self, pid: str, names: list[str]) -> None:
        self.log_message(pid, "you", "", files=names)
        await self.send_message(
            pid, "", log_user=False, prompt=f"(They dropped in {', '.join(names)}, saved in {self.files_dir(pid)}.)"
        )

    async def stop_chat(self, pid: str) -> bool:
        s = self.chats.get(pid)
        if s is None or not s.busy:
            return False
        try:
            await s.interrupt()
        except Exception:
            await s.close()
            self.chats.pop(pid, None)
        return True

    async def reset_chat(self, pid: str, *, clear: bool = False) -> None:
        lock = self.chat_locks.setdefault(pid, asyncio.Lock())
        if lock.locked():
            raise ValueError("Ken is still working. Stop or finish the reply before clearing this thread.")
        async with lock:
            # Archive by boundary, rather than deleting messages, transcripts or memory.
            if clear:
                boundary = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE project=?", (pid,)).fetchone()[0]
                self.kv_set(f"thread:{pid}", json.dumps({"after": boundary, "cleared_at": time.time()}))
            s = self.chats.pop(pid, None)
            self.db.execute("DELETE FROM kv WHERE k=?", (f"session:{pid}",))
            if s is not None:
                await s.close()
            self.broadcast("chat.reset", pid)


    # ------------------------------------------------------------- requests
    # Agents ask for things by dropping JSON into ~/.ken/requests/: a background
    # run, or the human's OK on something. Files, so any brain can do it.

    async def _watch_requests(self) -> None:
        while True:
            try:
                for f in sorted(self.requests_dir.glob("*.json")):
                    self._ingest(f)
            except Exception as e:
                log.warning("request watcher: %s", e)
            await asyncio.sleep(1)

    def _ingest(self, f: Path) -> None:
        try:
            if time.time() - f.stat().st_mtime < 0.3:
                return  # still being written
            self.handle_request(json.loads(f.read_text()))
            f.unlink(missing_ok=True)
        except Exception as e:
            log.warning("bad request %s: %s", f.name, e)
            bad = self.requests_dir / "rejected"
            bad.mkdir(exist_ok=True)
            f.rename(bad / f.name)
            (bad / (f.name + ".error.txt")).write_text(f"{e}\n")

    def find_project(self, name: str) -> str | None:
        want = str(name).strip().lower()
        for p in self.list_projects():
            if want in (p["id"], p["name"].lower(), slugify(p["name"])):
                return p["id"]
        return None

    def handle_request(self, req: dict) -> None:
        kind = req.get("type")
        if kind == "project":
            self.create_project(req.get("name", ""), req.get("path", ""), req.get("context", ""), req.get("blurb", ""))
            return
        pid = str(req.get("project", ""))
        if pid != HOME:
            pid = self.find_project(pid) or pid
            self.get_project(pid)
        if kind == "run" and pid != HOME:
            self.start_run(pid, str(req.get("title") or "Task")[:120], str(req["prompt"]), source="agent")
        elif kind == "approval":
            self.create_approval(pid, str(req.get("kind", "other")), str(req["title"]), str(req.get("body", "")), str(req.get("next", "")))
        else:
            raise ValueError(f"unknown request {kind!r}")

    # ----------------------------------------------------------------- runs

    def _run_row(self, rid: str) -> dict:
        r = self.db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if r is None:
            raise KeyError(rid)
        return dict(r)

    def get_run(self, rid: str) -> dict:
        run = self._run_row(rid)
        rows = self.db.execute("SELECT * FROM events WHERE run=? ORDER BY id LIMIT 400", (rid,)).fetchall()
        run["events"] = [{"id": r["id"], "ts": r["ts"], "type": r["type"], **json.loads(r["data"])} for r in rows]
        return run

    def list_runs(self, project: str | None = None, active: bool | None = None, since: float = 0, limit: int = 50) -> list[dict]:
        q, args = "SELECT * FROM runs WHERE created >= ?", [since]
        if project:
            q += " AND project=?"
            args.append(project)
        if active is True:
            q += " AND status IN ('queued','running')"
        elif active is False:
            q += " AND status NOT IN ('queued','running')"
        q += " ORDER BY COALESCE(finished, created) DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.db.execute(q, args).fetchall()]

    def start_run(self, pid: str, title: str, prompt: str, source: str = "you") -> dict:
        self.get_project(pid)
        rid = uuid.uuid4().hex[:10]
        self.db.execute(
            "INSERT INTO runs (id, project, title, prompt, source, status, created, activity) VALUES (?,?,?,?,?,?,?,?)",
            (rid, pid, title.strip()[:120] or "Task", prompt, source, "queued", time.time(), "waiting to start"),
        )
        self.emit("run.created", project=pid, run=rid, title=title)
        self.run_tasks[rid] = asyncio.create_task(self._execute(rid))
        return self._run_row(rid)

    def _set_run(self, rid: str, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), rid))

    async def _execute(self, rid: str) -> None:
        run = self._run_row(rid)
        pid = run["project"]
        async with self.run_slots:
            if self._run_row(rid)["status"] == "stopped":
                return
            self._set_run(rid, status="running", started=time.time(), activity="starting")
            self.emit("run.started", project=pid, run=rid)
            session = self.brain.session(
                cwd=str(self.workdir(self.get_project(pid))),
                system=lambda: self.system(pid) + f'\n\nBackground run: "{run["title"]}". Nobody is watching; '
                "don't ask questions. End with 1-3 lines on what happened, with links.",
            )
            self.run_sessions[rid] = session
            last = ""

            async def on_text(text: str) -> None:
                nonlocal last
                last = text
                self._set_run(rid, activity=text.strip().splitlines()[0][:140])
                self.emit("run.text", project=pid, run=rid, text=text)

            async def on_tool(line: str) -> None:
                self._set_run(rid, activity=line)
                self.emit("run.tool", project=pid, run=rid, text=line)

            status, summary = "failed", ""
            try:
                final = await asyncio.wait_for(session.ask(run["prompt"], on_text, on_tool), timeout=3600)
                status, summary = "done", (final or last or "Finished.").strip()
            except asyncio.CancelledError:
                status, summary = "stopped", (last or "Stopped.").strip()
            except Exception as e:
                log.warning("run %s failed: %s", rid, e)
                summary = "Timed out." if isinstance(e, asyncio.TimeoutError) else f"Failed: {e}"
            finally:
                self.run_sessions.pop(rid, None)
                try:
                    await session.close()
                except Exception:
                    pass
            if self._run_row(rid)["status"] == "stopped":
                status = "stopped"
            self._set_run(rid, status=status, finished=time.time(), summary=summary[:4000], activity="")
            self.emit("run.finished", project=pid, run=rid, status=status, title=run["title"], summary=summary[:600])
            if status != "stopped":
                self.log_message(pid, "ken", summary[:4000], run=rid, run_title=run["title"], run_status=status)

    async def stop_run(self, rid: str) -> None:
        run = self._run_row(rid)
        if run["status"] not in ("queued", "running"):
            return
        self._set_run(rid, status="stopped")
        session = self.run_sessions.get(rid)
        if session is not None:
            try:
                await asyncio.wait_for(session.interrupt(), timeout=5)
            except Exception:
                pass
        task = self.run_tasks.get(rid)
        if task is not None and not task.done():
            await asyncio.sleep(2)
            if not task.done():
                task.cancel()
        if run["status"] == "queued":
            self._set_run(rid, finished=time.time(), summary="Stopped before it started.")
            self.emit("run.finished", project=run["project"], run=rid, status="stopped", title=run["title"], summary="Stopped.")

    # ------------------------------------------------------------ approvals

    def create_approval(self, pid: str, kind: str, title: str, body: str, next_step: str = "") -> dict:
        aid = uuid.uuid4().hex[:10]
        self.db.execute(
            "INSERT INTO approvals (id, project, kind, title, body, next, status, created) VALUES (?,?,?,?,?,?,?,?)",
            (aid, pid, kind if kind in APPROVAL_KINDS else "other", title.strip()[:200], body.strip()[:20000],
             next_step.strip()[:2000], "pending", time.time()),
        )
        a = self.get_approval(aid)
        self.emit("approval.created", project=pid, approval=a)
        return a

    def get_approval(self, aid: str) -> dict:
        r = self.db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if r is None:
            raise KeyError(aid)
        return dict(r)

    def pending_approvals(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY created").fetchall()]

    async def resolve_approval(self, aid: str, decision: str, body: str | None = None) -> dict:
        a = self.get_approval(aid)
        if a["status"] != "pending":
            return a
        approved = decision == "approve"
        final = (body if body is not None else a["body"]).strip()
        self.db.execute(
            "UPDATE approvals SET status=?, resolved=?, final_body=? WHERE id=?",
            ("approved" if approved else "skipped", time.time(), final if approved else None, aid),
        )
        a = self.get_approval(aid)
        self.emit("approval.resolved", project=a["project"], approval=a)
        if approved:
            go = f'(Approved: "{a["title"]}". Do it now, exactly as below, then report the proof.\n\n{final}' + (
                f"\n\nThen: {a['next']})" if a["next"] else ")")
            if a["project"] == HOME:
                asyncio.create_task(self.send_message(HOME, "", prompt=go, log_user=False))
            else:
                self.start_run(a["project"], a["title"], go, source="approval")
        return a

    # ------------------------------------------------------------------- views

    def chats_list(self) -> list[dict]:
        """Ken first, then each project — like a chat app's list."""
        rows = [(HOME, "Ken", "#1B1A17")] + [(p["id"], p["name"], p["color"]) for p in self.list_projects()]
        out = []
        for pid, name, color in rows:
            last = self.messages(pid, 1)
            preview = ""
            if last:
                preview = last[0]["text"] or ("📎 " + ", ".join(last[0].get("files") or []))
            out.append({"id": pid, "name": name, "color": color, "last": preview[:120],
                        "last_ts": last[0]["ts"] if last else 0, "busy": self.chat_busy(pid)})
        return out

    def today(self) -> dict:
        running = self.list_runs(active=True)
        approvals = self.pending_approvals()
        projects = self.list_projects()
        for p in projects:
            p["running"] = sum(1 for r in running if r["project"] == p["id"]) + (1 if self.chat_busy(p["id"]) else 0)
            p["pending"] = sum(1 for a in approvals if a["project"] == p["id"])
        return {
            "projects": projects,
            "approvals": approvals,
            "running": running,
            "done": self.list_runs(active=False, since=time.time() - 86400, limit=12),
            "home": self.messages(HOME, 30),
            "home_busy": self.chat_busy(HOME),
        }

    def project_view(self, pid: str) -> dict:
        p = self.get_project(pid)
        return {
            "project": p,
            "context": self.get_context(pid),
            "autonomy": self.get_autonomy(pid),
            "files": self.list_files(pid),
            "runs": self.list_runs(project=pid, limit=20),
            "messages": self.messages(pid),
            "busy": self.chat_busy(pid),
            "approvals": [a for a in self.pending_approvals() if a["project"] == pid],
        }

    def chat_view(self, pid: str) -> dict:
        name = "Ken" if pid == HOME else self.get_project(pid)["name"]
        boundary = json.loads(self.kv_get(f"thread:{pid}") or "{}")
        messages = [m for m in self.messages(pid) if m["id"] > boundary.get("after", 0)]
        return {"id": pid, "name": name, "messages": messages, "busy": self.chat_busy(pid),
                "cleared_at": boundary.get("cleared_at", 0)}

    # ---------------------------------------------------------------------- kv

    def kv_get(self, k: str) -> str | None:
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else None

    def kv_set(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO kv (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
