"""The engine: Ken's conversations — one with Ken, one per agent — in one
place that every screen (the app, Telegram) reads from.

An agent is a folder, ~/.ken/agents/<id>/, holding everything it knows:
agent.json (its name, model, and code folder if it has one), CONTEXT.md (who
it is and what it's for — read with every message), jobs.json (its schedule),
tools.json (what it may use), autonomy.json, memory/ and files/. An agent sees
only its own folder. Ken itself (Home) is the chief of staff: it sees every
agent and can hand them work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from brains import Brain
import crash

log = logging.getLogger("ken")

HOME = "home"  # the conversation with Ken itself
WHERE = {"app": "written in the Ken app on this computer", "telegram": "written on their phone (Telegram)"}
COLORS = ["#1F4E79", "#7A4E8C", "#5E7A3A", "#9C4A1A", "#2E6F73", "#8A3B5C", "#4A4F8C", "#6B5B2E"]
GREET_AFTER = 3 * 3600  # speak first when a chat is opened after this long
# Ken's very first contact, the same wherever it happens (the app or the phone): Ken writes it,
# from what it knows, following the awakening in its instructions.
def first_contact_prompt(how: str) -> str:
    return f"(This is your very first contact with them: they just {how}. Begin.)"

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
BUILTIN_TOOLS = ("browser", "email", "calendar")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "project"


def safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f]", "", name).lstrip(".")
    return name[:180]


def memory_listing(folder: Path) -> str:
    """Each note's filename and first line: enough to know what's there without opening it."""
    lines = []
    for p in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            first = next((ln.strip().lstrip("#").strip() for ln in p.read_text(errors="replace").splitlines() if ln.strip()), "")
        except OSError:
            first = ""
        lines.append(f"  {p.name} — {first[:120]}" if first else f"  {p.name}")
    return "\n".join(lines) or "  (empty)"


class Engine:
    def __init__(self, home: Path, brain: Brain) -> None:
        self.home = Path(home)
        self.brain = brain
        self.agents_dir = self.home / "agents"
        self.requests_dir = self.home / "requests"
        self.db = sqlite3.connect(self.home / "app.db", isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.subscribers: set[asyncio.Queue] = set()
        self.chats: dict[str, object] = {}
        self.chat_locks: dict[str, asyncio.Lock] = {}
        self.chat_tools: dict[str, float] = {}  # when each open agent chat last read its tools.json
        self.home_cwd = self.home / "work"
        self.home_system: Callable[[], str] = lambda: ""
        self.agent_base: Callable[[], str] = lambda: ""
        # The built-in tools ("browser", "email", "calendar") an agent may be given: MCP servers, and their prompt.
        self.tools: Callable[[set], dict] = lambda only: {}
        self.tools_prompt: Callable[[set], str] = lambda only: ""
        self.history_hook: Callable[[str, str], None] | None = None
        self.new_install: Callable[[], bool] = lambda: False  # true until onboarding has begun
        self._greeting: set[str] = set()
        self.run_tasks: dict[str, asyncio.Task] = {}
        self.run_sessions: dict[str, object] = {}
        self.run_slots = asyncio.Semaphore(4)
        self._bg: list[asyncio.Task] = []

    def configure_home(self, cwd: Path, system: Callable[[], str], project_system: Callable[[], str] | None = None) -> None:
        self.home_cwd = Path(cwd)
        self.home_system = system
        self.agent_base = project_system or system

    def start(self) -> None:
        try:
            self.migrate()
        except Exception as e:  # never keep Ken from starting; the next start tries again
            log.warning("moving projects to agents failed: %s", e)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()  # a restart ends whatever was running; say so instead of leaving ghosts
        self.db.execute(
            "UPDATE runs SET status='stopped', finished=?, summary=COALESCE(summary,'Interrupted by a restart.') "
            "WHERE status IN ('running','queued')", (now,),
        )
        self._bg.append(asyncio.create_task(self._watch_requests()))

    def migrate(self) -> None:
        """Agents used to be called projects and live in ~/.ken/projects. Move them over once,
        safe to repeat: never overwrites, and leaves `projects` as a link so old paths still work."""
        old = self.home / "projects"
        moved: list[str] = []
        if old.is_dir() and not old.is_symlink():
            if not self.agents_dir.exists():
                moved = [d.name for d in old.iterdir() if d.is_dir()]
                old.rename(self.agents_dir)
            else:
                for d in old.iterdir():
                    if not (self.agents_dir / d.name).exists():
                        d.rename(self.agents_dir / d.name)
                        moved.append(d.name)
                try:
                    old.rmdir()
                except OSError:
                    pass  # a name was taken in both; leave the rest where it is
        if not os.path.lexists(old) and self.agents_dir.is_dir():
            try:
                old.symlink_to(self.agents_dir.name)
            except OSError:
                pass
        if not self.agents_dir.is_dir():
            return
        for d in self.agents_dir.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            if (d / "project.json").exists() and not (d / "agent.json").exists():
                (d / "project.json").rename(d / "agent.json")
            (d / "memory").mkdir(exist_ok=True)
        for name in moved:
            self._carry_claude_sessions(os.path.realpath(self.home) + f"/projects/{name}", self.agents_dir / name)

    def _carry_claude_sessions(self, old_cwd: str, new_dir: Path) -> None:
        """Claude Code files a folder's past conversations under its path; a moved folder
        would start over with no memory of them. Copy them to where the new path looks."""
        try:
            if json.loads((new_dir / "agent.json").read_text()).get("path"):
                return  # it works in its own code folder, which didn't move
        except (OSError, ValueError):
            return
        store = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
        enc = lambda p: re.sub(r"[^A-Za-z0-9]", "-", str(p))  # noqa: E731 — how Claude Code names them
        src, dst = store / enc(old_cwd), store / enc(os.path.realpath(new_dir))
        if src.is_dir() and not dst.exists():
            try:
                shutil.copytree(src, dst)
            except OSError as e:
                log.warning("couldn't carry Claude's history for %s: %s", new_dir.name, e)

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

    async def first_contact(self) -> None:
        """A brand-new install's Home chat was opened: Ken speaks first, exactly as it does on Telegram."""
        if not self.new_install() or HOME in self._greeting or self.chat_busy(HOME) or self.db.execute(
                "SELECT 1 FROM events WHERE project=? AND type='message' LIMIT 1", (HOME,)).fetchone():
            return
        self._greeting.add(HOME)
        try:
            await self.send_message(HOME, "", log_user=False, via="app",
                                    prompt=first_contact_prompt("opened the Ken app on this computer for the first time"))
        finally:
            self._greeting.discard(HOME)

    def log_message(self, pid: str, role: str, text: str, **extra) -> dict:
        if pid == HOME and self.history_hook is not None and text:
            self.history_hook(role, text)  # the daily transcript that nightly memory reads
        return self.emit("message", project=pid, role=role, text=text, **extra)

    # ------------------------------------------------------------------- agents

    def _adir(self, pid: str) -> Path:
        if pid == HOME or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", pid or ""):
            raise KeyError(pid)
        return self.agents_dir / pid

    def get_agent(self, pid: str) -> dict:
        d = self._adir(pid)
        try:
            meta = json.loads((d / "agent.json").read_text())
        except OSError:
            try:  # not moved over yet (see migrate)
                meta = json.loads((d / "project.json").read_text())
            except (OSError, ValueError):
                raise KeyError(pid)
        except ValueError:
            raise KeyError(pid)
        meta["id"] = pid
        meta["workdir"] = str(self.workdir(meta))
        return meta

    def list_agents(self) -> list[dict]:
        if not self.agents_dir.exists():
            return []
        out = []
        for d in self.agents_dir.iterdir():
            try:
                out.append(self.get_agent(d.name))
            except KeyError:
                continue
        return sorted(out, key=lambda p: p.get("created", 0))

    def workdir(self, meta: dict) -> Path:
        """An agent always lives and works in its own folder under Ken. A code or info folder
        ("path") is a place it goes to for the job, named in its prompt, never its home."""
        return self.agents_dir / meta["id"]

    def code_dir(self, meta: dict) -> Path | None:
        return Path(os.path.expanduser(meta["path"])) if meta.get("path") else None

    def create_agent(self, name: str, path: str = "", context: str = "", blurb: str = "") -> dict:
        name = name.strip()[:60]
        if not name:
            raise ValueError("an agent needs a name")
        path = path.strip()
        if path and not Path(os.path.expanduser(path)).is_dir():
            raise ValueError(f"folder not found: {path}")
        base = slugify(name)
        if base == HOME:
            base = "home-agent"
        pid, n = base, 2
        while (self.agents_dir / pid).exists():
            pid, n = f"{base}-{n}", n + 1
        d = self.agents_dir / pid
        (d / "files").mkdir(parents=True)
        (d / "memory").mkdir()
        meta = {"name": name, "path": path, "blurb": blurb.strip()[:200],
                "color": COLORS[len(self.list_agents()) % len(COLORS)], "created": time.time()}
        (d / "agent.json").write_text(json.dumps(meta, indent=1) + "\n")
        (d / "CONTEXT.md").write_text(f"# {name}\n\n{context.strip()}\n")
        self.emit("project.created", project=pid, name=name)
        return self.get_agent(pid)

    def get_context(self, pid: str) -> str:
        try:
            return (self._adir(pid) / "CONTEXT.md").read_text()
        except OSError:
            return ""

    def set_context(self, pid: str, text: str) -> None:
        (self._adir(pid) / "CONTEXT.md").write_text(text.rstrip() + "\n")
        self.emit("project.updated", project=pid)

    def update_agent(self, pid: str, **fields) -> dict:
        meta = self.get_agent(pid)
        path = fields.get("path")
        if path and not Path(os.path.expanduser(str(path).strip())).is_dir():
            raise ValueError(f"folder not found: {path}")
        if fields.get("name") is not None and not str(fields["name"]).strip():
            raise ValueError("an agent needs a name")
        for k in ("name", "blurb", "path", "model"):
            if fields.get(k) is not None:
                meta[k] = str(fields[k]).strip()[:60 if k == "name" else 500]
        if path is not None:
            self.chats.pop(pid, None)  # the next message gets the new folder in its prompt
        meta.pop("id", None)
        meta.pop("workdir", None)
        (self._adir(pid) / "agent.json").write_text(json.dumps(meta, indent=1) + "\n")
        self.emit("project.updated", project=pid)
        return self.get_agent(pid)

    async def remove_agent(self, pid: str) -> None:
        """Take an agent off the list. Its chat and notes move to agents/.removed (recoverable);
        the folder it worked in is never touched."""
        self.get_agent(pid)
        if self.chat_busy(pid):
            raise ValueError("This agent is working. Stop it first.")
        s = self.chats.pop(pid, None)
        if s is not None:
            await s.close()
        trash = self.agents_dir / ".removed"
        trash.mkdir(exist_ok=True)
        self._adir(pid).rename(trash / f"{pid}-{int(time.time())}")
        self.emit("project.removed", project=pid)

    def get_autonomy(self, pid: str) -> dict:
        try:
            data = json.loads((self._adir(pid) / "autonomy.json").read_text())
            return {"alone": list(data.get("alone", [])), "ask": list(data.get("ask", []))}
        except (OSError, ValueError):
            return {k: list(v) for k, v in DEFAULT_AUTONOMY.items()}

    def set_autonomy(self, pid: str, alone: list, ask: list) -> None:
        self.get_agent(pid)
        clean = lambda xs: [str(x).strip()[:60] for x in xs if str(x).strip()][:30]  # noqa: E731
        (self._adir(pid) / "autonomy.json").write_text(json.dumps({"alone": clean(alone), "ask": clean(ask)}, indent=1) + "\n")
        self.emit("project.updated", project=pid)

    # -------------------------------------------------------------------- files

    def files_dir(self, pid: str) -> Path:
        return self.home / "inbox" if pid == HOME else self._adir(pid) / "files"

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
            self.get_agent(pid)
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
        """Ken's own system prompt (the chief of staff); an agent gets a work prompt and only its own folder."""
        if pid == HOME:
            return f"{self.home_system()}\n\n{self.kit(pid)}"
        p = self.get_agent(pid)
        d = self._adir(pid)
        auto = self.get_autonomy(pid)
        tools = self.tools_config(pid)
        r = self.requests_dir
        extra = ", ".join(tools["mcp"]) or "none yet"
        where = (f" The code and files for your job are in {p['path']}: go there when a task needs them, and leave "
                 "anything there you didn't make for this job as it is.") if p.get("path") else ""
        return (
            f"{self.agent_base()}\n\n=== You are {p['name']}, one of their agents ===\n"
            "They set you up in Ken for one job, described in CONTEXT.md below. Ken, their main assistant, "
            "is your chief of staff and may hand you work. Stay on your job. You don't see their other agents "
            f"or Ken's memory: everything you know lives in your folder, {d}.\n"
            f"- CONTEXT.md: who you are, your goal, your rules (below). Read with every message; keep it current.\n"
            f"- memory/: your notes, one topic per file, its first line saying what it holds. Now:\n{memory_listing(d / 'memory')}\n"
            f"- jobs.json: your schedule, a JSON array like {{\"name\": \"daily-report\", \"time\": \"08:00\", \"days\": \"daily\", \"prompt\": \"...\"}} "
            '("days": daily, weekdays or e.g. "mon,thu"; "on": false pauses one). Each job runs here, in this chat, at '
            "this computer's local time. Add, change or remove jobs when they ask.\n"
            f"- tools.json: what you may use. \"use\" picks Ken's built-in tools ({', '.join(BUILTIN_TOOLS)}); "
            "\"mcp\" adds any MCP server, in Claude Code's mcpServers format, to give yourself a new tool (it's ready "
            f"from the next message). Now: {', '.join(tools['use']) or 'no built-ins'}; extra servers: {extra}.\n"
            f"- files/: what they drop into this chat.\n"
            f"- Keys: {self._keys_line(pid)} They add keys in this agent's Keys (the ⋯ menu next to its name). When you "
            "need a login or API key, ask them to add it there under a name you suggest (like STRIPE_KEY), or a whole "
            "file like a service-account JSON. Never ask for a secret in this chat, never print one, never copy one "
            "anywhere else.\n"
            f"Your home and working folder is {p['workdir']}.{where}\n"
            f"Fine to do on your own: {'; '.join(auto['alone']) or 'nothing'}. Ask first: {'; '.join(auto['ask']) or 'nothing'} "
            f"(rules in {d / 'autonomy.json'}). To ask, or to hand long work to the background, write JSON into {r}/: "
            f'{{"type": "approval", "agent": "{pid}", "kind": "email|post|merge|spend|other", "title": "...", "body": "exactly what they\'ll see", "next": "what you do once approved"}} or '
            f'{{"type": "run", "agent": "{pid}", "title": "...", "prompt": "full instructions"}}.\n'
            + (f"\n{self.tools_prompt(set(tools['use']))}\n" if self.tools_prompt(set(tools["use"])) else "")
            + f"\n=== CONTEXT.md ===\n{self.get_context(pid).strip()}"
        )

    def kit(self, pid: str) -> str:
        """What Ken can use beyond what's connected in Settings: extra tools and its keys."""
        extra = ", ".join(self.tools_config(pid)["mcp"]) or "none yet"
        return (
            f"FEEDBACK FOR THE PEOPLE WHO MAKE KEN: when they want to tell the Ken team something (a bug, an idea, a "
            f"complaint), write it up in their words, show them exactly what will be sent, and only after they say yes "
            f'write {{"type": "feedback", "text": "...", "email": "their email, only if they want a reply"}} into '
            f"{self.requests_dir}/. Say it was sent. They can also use Settings → Send feedback.\n"
            f"=== Your tools and keys ===\nExtra tools: {extra}. To add one, put an MCP server in \"mcp\" of "
            f"{self._tools_file(pid)} (Claude Code's mcpServers format); it's ready from the next message. A CLI "
            "installed on this computer works as is. "
            f"Keys: {self._keys_line(pid)} They add keys in the app (the Brain tab at the top, then Keys). When you need a "
            "login or API key, ask them to add it there under a name you suggest (like STRIPE_KEY), or a whole file "
            "like a service-account JSON. Never ask for a secret in chat, never print one, never copy one anywhere else."
        )

    def _keys_line(self, pid: str) -> str:
        keys = self.list_keys(pid)
        values = [k["name"] for k in keys if k["kind"] == "value"]
        files = [k["name"] for k in keys if k["kind"] == "file"]
        if not keys:
            return "none yet."
        line = ""
        if values:
            line += "environment variables " + ", ".join(f"${v}" for v in values) + " (use them in commands; the value is never shown to you here). "
        if files:
            line += "files " + ", ".join(str(self.keys_dir(pid) / "files" / f) for f in files) + ". "
        return line

    def chief_brief(self) -> str:
        """What Ken, as chief of staff, knows about every agent."""
        r = self.requests_dir
        lines = [
            "=== Their agents: you are their chief of staff ===",
            f"Each agent is a chat with one job and its own folder, {self.agents_dir}/<id>/ (CONTEXT.md, memory/, "
            "jobs.json, tools.json, files/). An agent sees only its own folder; you see all of them, so open an "
            "agent's files whenever you need the detail, and keep an eye on whether they're getting their jobs done. "
            f'To hand an agent work in the background, write {{"type": "run", "agent": "<id>", "title": "...", "prompt": "..."}} into {r}/. '
            'When they want a new agent, write {"type": "agent", "name": "...", "context": "what it is for, in their words", '
            '"path": "the code or info folder its job is about, if any"}; it opens its own chat and sets itself up there.',
        ]
        running = self.list_runs(active=True)
        for a in self.list_agents():
            pid = a["id"]
            about = next((ln.strip() for ln in self.get_context(pid).splitlines() if ln.strip() and not ln.startswith("#")), "")
            jobs = [j.get("name", "?") for j in self.agent_jobs(pid)]
            last = self._last_message_ts(pid)
            busy = sum(1 for x in running if x["project"] == pid) + (1 if self.chat_busy(pid) else 0)
            lines.append(
                f"- {a['name']} ({pid}): {about[:200] or 'no context yet'}"
                + (f" · last active {time.strftime('%a %d %b %H:%M', time.localtime(last))}" if last else " · not used yet")
                + (f" · {busy} working now" if busy else "")
                + (f" · jobs: {', '.join(jobs)}" if jobs else "")
            )
        if len(lines) == 2:
            lines.append("They have no agents yet.")
        return "\n".join(lines)

    # -------------------------------------------------------------- jobs, tools

    def agent_jobs(self, pid: str) -> list[dict]:
        try:
            jobs = json.loads((self._adir(pid) / "jobs.json").read_text())
        except (OSError, ValueError, KeyError):
            return []
        return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []

    def _tools_file(self, pid: str) -> Path:
        return self.home / "tools.json" if pid == HOME else self._adir(pid) / "tools.json"

    def tools_config(self, pid: str) -> dict:
        """Its tools.json (Ken's is ~/.ken/tools.json). Without one it gets every built-in tool."""
        try:
            data = json.loads(self._tools_file(pid).read_text())
        except (OSError, ValueError):
            data = {}
        use = data.get("use", list(BUILTIN_TOOLS)) if isinstance(data, dict) else list(BUILTIN_TOOLS)
        mcp = data.get("mcp", {}) if isinstance(data, dict) else {}
        return {"use": [t for t in use if t in BUILTIN_TOOLS], "mcp": mcp if isinstance(mcp, dict) else {}}

    def agent_tools(self, pid: str) -> dict:
        cfg = self.tools_config(pid)
        return {**self.tools(set(cfg["use"])), **cfg["mcp"]}

    def _tools_stamp(self, pid: str) -> tuple:
        """Changes when its tools or keys do, so an open chat reopens with them."""
        out = []
        for f in (self._tools_file(pid), self.keys_dir(pid) / "keys.json", self.keys_dir(pid) / "files"):
            try:
                out.append(f.stat().st_mtime)
            except OSError:
                out.append(0.0)
        return tuple(out)

    # -------------------------------------------------------------------- about
    # Everything a chat knows, to see, edit and share: who it is (Ken's SOUL.md, an agent's
    # CONTEXT.md), its memory notes, its tools, keys (names only) and files.

    def context_file(self, pid: str) -> Path:
        return self.home_cwd / "SOUL.md" if pid == HOME else self._adir(pid) / "CONTEXT.md"

    def memory_dir(self, pid: str) -> Path:
        return self.home / "memory" if pid == HOME else self._adir(pid) / "memory"

    def about(self, pid: str) -> dict:
        try:
            context = self.context_file(pid).read_text()
        except OSError:
            context = ""
        notes = []
        d = self.memory_dir(pid)
        for f in sorted(d.glob("*.md")) if d.is_dir() else []:
            first = next((ln.strip().lstrip("#").strip() for ln in f.read_text(errors="replace").splitlines() if ln.strip()), "")
            notes.append({"name": f.name, "about": first[:140]})
        out = {"id": pid, "name": "Ken" if pid == HOME else self.get_agent(pid)["name"], "context": context,
               "memory": notes, "files": self.list_files(pid)}
        out.update(tools=self.tools_config(pid), builtin=list(BUILTIN_TOOLS), keys=self.list_keys(pid),
                   path="" if pid == HOME else self.get_agent(pid).get("path", ""))
        return out

    def set_about_context(self, pid: str, text: str) -> None:
        f = self.context_file(pid)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text.rstrip() + "\n")
        self.emit("project.updated", project=pid)

    def _note(self, pid: str, name: str) -> Path:
        clean = safe_filename(name)
        if not clean or clean != name or not clean.endswith(".md"):
            raise KeyError(name)
        return self.memory_dir(pid) / clean

    def read_note(self, pid: str, name: str) -> str:
        try:
            return self._note(pid, name).read_text()
        except OSError:
            raise KeyError(name) from None

    def write_note(self, pid: str, name: str, text: str) -> None:
        f = self._note(pid, name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text.rstrip() + "\n")

    def remove_note(self, pid: str, name: str) -> None:
        self._note(pid, name).unlink(missing_ok=True)

    def set_tools_use(self, pid: str, use: list) -> dict:
        cfg = self.tools_config(pid)
        cfg["use"] = [t for t in BUILTIN_TOOLS if t in use]
        self._tools_file(pid).write_text(json.dumps(cfg, indent=1) + "\n")
        self.emit("project.updated", project=pid)
        return cfg

    def export_agent(self, pid: str) -> bytes:
        """The agent as one file to share: who it is, memory, jobs, tools and rules. Never its keys,
        its files, or anything only meaningful on this computer (its code folder)."""
        import io
        import zipfile

        p, d = self.get_agent(pid), self._adir(pid)
        meta = {k: p[k] for k in ("name", "blurb", "color", "model") if p.get(k)}
        tools = self.tools_config(pid)
        tools["mcp"] = {k: {f: v for f, v in (c.items() if isinstance(c, dict) else []) if f not in ("env", "headers")}
                        for k, c in tools["mcp"].items()}  # a server's env and headers usually hold secrets
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("agent.json", json.dumps(meta, indent=1))
            z.writestr("tools.json", json.dumps(tools, indent=1))
            for name in ("CONTEXT.md", "jobs.json", "autonomy.json"):
                if (d / name).is_file():
                    z.writestr(name, (d / name).read_text())
            for f in sorted((d / "memory").glob("*.md")) if (d / "memory").is_dir() else []:
                z.writestr(f"memory/{f.name}", f.read_text())
        return buf.getvalue()

    def import_agent(self, data: bytes) -> dict:
        import io
        import zipfile

        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            meta = json.loads(z.read("agent.json"))
        except (zipfile.BadZipFile, KeyError, ValueError):
            raise ValueError("That isn't a Ken agent file.") from None
        a = self.create_agent(str(meta.get("name") or "Agent"), blurb=str(meta.get("blurb", "")))
        d = self._adir(a["id"])
        for name in z.namelist():
            folder, _, base = name.rpartition("/")
            if (folder, base) in (("", "CONTEXT.md"), ("", "jobs.json"), ("", "autonomy.json"), ("", "tools.json")) or (
                    folder == "memory" and base == safe_filename(base) and base.endswith(".md")):
                (d / name).write_bytes(z.read(name)[:1_000_000])
        if meta.get("model"):
            self.update_agent(a["id"], model=meta["model"])
        return self.get_agent(a["id"])

    # --------------------------------------------------------------------- keys
    # An agent's secrets live outside its folder, in ~/.ken/credentials/agents/<id>/ (only you
    # can read them): values in keys.json, whole files in files/. The agent gets the values as
    # environment variables and the files as paths; it never sees a value in its prompt, and
    # any value it writes is masked before it reaches a screen.

    def keys_dir(self, pid: str) -> Path:
        if pid == HOME:
            return self.home / "credentials" / "ken"
        self._adir(pid)
        return self.home / "credentials" / "agents" / pid

    def _key_values(self, pid: str) -> dict:
        try:
            data = json.loads((self.keys_dir(pid) / "keys.json").read_text())
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError, KeyError):
            return {}

    def _private_dir(self, d: Path) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        for p in (self.home / "credentials", d.parent, d):
            if p.is_relative_to(self.home / "credentials") and p.is_dir():
                os.chmod(p, 0o700)
        return d

    def _write_private(self, path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        tmp.replace(path)

    def list_keys(self, pid: str) -> list[dict]:
        files = self.keys_dir(pid) / "files"
        out = [{"name": k, "kind": "value"} for k in sorted(self._key_values(pid))]
        if files.is_dir():
            out += [{"name": f.name, "kind": "file"} for f in sorted(files.iterdir()) if f.is_file() and not f.name.endswith(".tmp")]
        return out

    def set_key(self, pid: str, name: str, value: str) -> list[dict]:
        name = re.sub(r"[^A-Z0-9_]", "_", name.strip().upper())
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", name):
            raise ValueError("Give it a name like STRIPE_KEY.")
        if not value.strip():
            raise ValueError("Paste the key's value.")
        values = {**self._key_values(pid), name: value.strip()}
        d = self._private_dir(self.keys_dir(pid))
        self._write_private(d / "keys.json", json.dumps(values, indent=1).encode())
        self.emit("project.updated", project=pid)
        return self.list_keys(pid)

    def set_key_file(self, pid: str, filename: str, data: bytes) -> list[dict]:
        name = safe_filename(filename)
        if not name:
            raise ValueError("That file needs a name.")
        d = self._private_dir(self.keys_dir(pid) / "files")
        self._write_private(d / name, data)
        os.utime(d)  # the folder's time is how an open chat notices
        self.emit("project.updated", project=pid)
        return self.list_keys(pid)

    def remove_key(self, pid: str, name: str) -> list[dict]:
        values = self._key_values(pid)
        if name in values:
            values.pop(name)
            self._write_private(self.keys_dir(pid) / "keys.json", json.dumps(values, indent=1).encode())
        else:
            f = self.keys_dir(pid) / "files" / safe_filename(name)
            if not safe_filename(name) or not f.is_file():
                raise KeyError(name)
            f.unlink()
            os.utime(f.parent)
        self.emit("project.updated", project=pid)
        return self.list_keys(pid)

    def mask(self, pid: str, text: str) -> str:
        """Hide any of the agent's key values it wrote, before the text is shown or stored."""
        if not text:
            return text
        for v in self._key_values(pid).values():
            if len(v) >= 6:
                text = text.replace(v, "••••••")
        return text

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

    async def state(self, pid: str, via: str | None = None) -> str:
        """A one-line snapshot sent with each message so replies can be specific."""
        parts = [time.strftime("%a %d %b %H:%M")]
        if via in WHERE:
            parts.append(WHERE[via])
        if pid != HOME:
            since = self._last_message_ts(pid)
            new = [f["name"] for f in self.list_files(pid) if f["mtime"] > since]
            if new:
                parts.append("new files: " + ", ".join(new[:10]))
            code = self.code_dir(self.get_agent(pid))
            git = await self._git(code) if code else ""
            if git:
                parts.append(git)
        return "[" + " · ".join(parts) + "]"

    # -------------------------------------------------------------------- chats

    def _chat_session(self, pid: str):
        s = self.chats.get(pid)
        if s is not None and self.chat_tools.get(pid) != self._tools_stamp(pid) and not s.busy:
            self.chats.pop(pid)  # its tools or keys changed: reopen with the new ones
            self._bg.append(asyncio.create_task(s.close()))
            s = None
        if s is None:
            self.chat_tools[pid] = self._tools_stamp(pid)
            if pid == HOME:
                s = self.brain.session(cwd=str(self.home_cwd), system=lambda: self.system(pid), resume=self.kv_get(f"session:{pid}"),
                                       mcp_fn=lambda: self.agent_tools(pid), env_fn=lambda: self._key_values(pid))
            else:
                s = self.brain.session(
                    cwd=str(self.workdir(self.get_agent(pid))), system=lambda: self.system(pid),
                    resume=self.kv_get(f"session:{pid}"),
                    model_fn=lambda: self.get_agent(pid).get("model") or self.brain.model_fn(),
                    mcp_fn=lambda: self.agent_tools(pid),
                    env_fn=lambda: self._key_values(pid),
                )
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
        job: dict | None = None,
    ) -> None:
        """Talk to Ken (or Ken-on-a-project). Replies stream to every screen;
        `listener` also gets each finished utterance (Telegram uses it)."""
        if pid != HOME:
            self.get_agent(pid)
        files = [f for f in (files or []) if f]
        if log_user:
            self.log_message(pid, "you", text, files=files, **({"via": via} if via else {}))
        body = prompt if prompt is not None else text
        if files and prompt is None:
            body += f"\n\n(Attached in {self.files_dir(pid)}: {', '.join(files)})"
        body = f"{await self.state(pid, via)}\n{body}"
        lock = self.chat_locks.setdefault(pid, asyncio.Lock())
        async with lock:
            self.broadcast("chat.busy", pid, busy=True)
            session = self._chat_session(pid)

            async def on_text(t: str) -> None:
                if t.strip() == "NOTHING_TO_SAY":
                    return
                t = self.mask(pid, t)
                if not quiet:
                    self.log_message(pid, "ken", t, **({"job": job} if job else {}))  # a scheduled job's report says so
                if listener is not None:
                    await listener(t)

            async def on_tool(line: str) -> None:
                self.broadcast("chat.tool", pid, text=self.mask(pid, line))

            async def on_delta(t: str) -> None:
                if not quiet:
                    self.broadcast("chat.delta", pid, text=self.mask(pid, t))

            started, outcome = time.monotonic(), "ok"
            try:
                await asyncio.wait_for(session.ask(body, on_text, on_tool, on_delta), timeout=1800)
            except asyncio.CancelledError:
                outcome = "stopped"
                raise
            except Exception as e:
                outcome = "timeout" if isinstance(e, asyncio.TimeoutError) else "error"
                if outcome == "error":
                    crash.report(e, f"chat failed: {e}", where="engine.send_message")
                log.warning("chat %s failed: %s", pid, e)
                await session.close()
                self.chats.pop(pid, None)
                msg = "That took too long, so I stopped." if isinstance(e, asyncio.TimeoutError) else f"Something went wrong: {e}"
                self.log_message(pid, "ken", msg, error=True)
                if listener is not None:
                    await listener(msg)
            finally:
                crash.track("message", via=via or ("job" if job else "app"), agent=pid != HOME, files=len(files),
                            spoke_first=not log_user and not job, outcome=outcome, seconds=round(time.monotonic() - started, 1))
                if session.session_id:
                    self.kv_set(f"session:{pid}", session.session_id)
                self.broadcast("chat.busy", pid, busy=False)

    async def on_open(self, pid: str) -> None:
        """A chat was opened: warm up, and if it's been a while, speak first."""
        if pid != HOME:
            self.get_agent(pid)
        await self.warm(pid)
        if self.chat_busy(pid) or pid in self._greeting or time.time() - self._last_message_ts(pid) < GREET_AFTER:
            return
        self._greeting.add(pid)
        try:
            await self.send_message(pid, "", log_user=False, prompt="(They just opened this chat. You speak first.)")
        finally:
            self._greeting.discard(pid)

    async def setup_agent(self, pid: str) -> None:
        """A new agent's first turn: it reads what it's for and sets itself up, in its own chat."""
        await self.send_message(pid, "", log_user=False, prompt=(
            "(You were just created; CONTEXT.md holds what they asked for. Set yourself up now: rewrite CONTEXT.md "
            "into a clear brief of your job, goal and rules. If the job has a rhythm, put jobs in jobs.json. Write "
            "tools.json with only the built-in tools you need, plus any MCP server that would help. Then tell them in "
            "a few short lines what you'll do and when, and ask for the first thing you need from them (a login, a "
            "key, a link), one thing at a time. Keys go in this agent's Keys (the ⋯ menu next to its name), never in this chat.)"
        ))

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

    def find_agent(self, name: str) -> str | None:
        want = str(name).strip().lower()
        for p in self.list_agents():
            if want in (p["id"], p["name"].lower(), slugify(p["name"])):
                return p["id"]
        return None

    def handle_request(self, req: dict) -> None:
        kind = req.get("type")
        if kind == "feedback":  # they asked Ken to pass something on to the people who make it
            crash.feedback(str(req.get("text", "")), str(req.get("email", "")))
            return
        if kind in ("agent", "project"):  # "project": what agents were called before
            a = self.create_agent(req.get("name", ""), req.get("path", ""), req.get("context", ""), req.get("blurb", ""))
            self._bg.append(asyncio.create_task(self.setup_agent(a["id"])))
            return
        pid = str(req.get("agent", req.get("project", "")))
        if pid != HOME:
            pid = self.find_agent(pid) or pid
            self.get_agent(pid)
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
        self.get_agent(pid)
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
                cwd=str(self.workdir(self.get_agent(pid))),
                system=lambda: self.system(pid) + f'\n\nBackground run: "{run["title"]}". Nobody is watching; '
                "don't ask questions. End with 1-3 lines on what happened, with links.",
                model_fn=lambda: self.get_agent(pid).get("model") or self.brain.model_fn(),
                mcp_fn=lambda: self.agent_tools(pid),
                env_fn=lambda: self._key_values(pid),
            )
            self.run_sessions[rid] = session
            last = ""

            async def on_text(text: str) -> None:
                nonlocal last
                text = self.mask(pid, text)
                last = text
                self._set_run(rid, activity=text.strip().splitlines()[0][:140])
                self.emit("run.text", project=pid, run=rid, text=text)

            async def on_tool(line: str) -> None:
                line = self.mask(pid, line)
                self._set_run(rid, activity=line)
                self.emit("run.tool", project=pid, run=rid, text=line)

            status, summary = "failed", ""
            try:
                final = await asyncio.wait_for(session.ask(run["prompt"], on_text, on_tool), timeout=3600)
                status, summary = "done", self.mask(pid, (final or last or "Finished.").strip())
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
        rows = [(HOME, "Ken", "#1B1A17")] + [(p["id"], p["name"], p["color"]) for p in self.list_agents()]
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
        projects = self.list_agents()
        for p in projects:
            p["running"] = sum(1 for r in running if r["project"] == p["id"]) + (1 if self.chat_busy(p["id"]) else 0)
            p["pending"] = sum(1 for a in approvals if a["project"] == p["id"])
        return {
            "agents": projects,
            "approvals": approvals,
            "running": running,
            "done": self.list_runs(active=False, since=time.time() - 86400, limit=12),
            "home": self.messages(HOME, 30),
            "home_busy": self.chat_busy(HOME),
        }

    def agent_view(self, pid: str) -> dict:
        p = self.get_agent(pid)
        return {
            "agent": p,
            "tools": self.tools_config(pid),
            "jobs": self.agent_jobs(pid),
            "context": self.get_context(pid),
            "autonomy": self.get_autonomy(pid),
            "files": self.list_files(pid),
            "runs": self.list_runs(project=pid, limit=20),
            "messages": self.messages(pid),
            "busy": self.chat_busy(pid),
            "approvals": [a for a in self.pending_approvals() if a["project"] == pid],
        }

    def chat_view(self, pid: str) -> dict:
        name = "Ken" if pid == HOME else self.get_agent(pid)["name"]
        boundary = json.loads(self.kv_get(f"thread:{pid}") or "{}")
        messages = [m for m in self.messages(pid) if m["id"] > boundary.get("after", 0)]
        return {"id": pid, "name": name, "messages": messages, "busy": self.chat_busy(pid) or pid in self._greeting,
                "cleared_at": boundary.get("cleared_at", 0)}

    # ---------------------------------------------------------------------- kv

    def kv_get(self, k: str) -> str | None:
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else None

    def kv_set(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO kv (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
