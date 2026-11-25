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
import contextlib
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

from brains import AIUnavailable, Brain
import crash
import usage

log = logging.getLogger("ken")

HOME = "home"  # the conversation with Ken itself
WHERE = {"app": "written in the Ken app on this computer", "telegram": "written on their phone (Telegram)",
         "voice": "said out loud on a voice call, passed on by your voice side, which speaks your answer back to them: keep it short and speakable"}
# An agent's picture: the emoji it picks for itself as it sets up ("emoji" in agent.json). Until then, or for one
# made before this, a guess from its name and brief; no match, and the apps show its first letter.
EMOJI_GUESSES = [
    (r"insta|reel|tiktok|photo|camera", "📸"), (r"video|youtube|film", "🎬"), (r"twitter|tweet|\bx\b|post|social|linkedin", "📣"),
    (r"inbox|mail|email|newsletter", "✉️"), (r"calendar|meeting|schedule", "📅"), (r"stock|invest|trade|crypto|market", "📈"),
    (r"money|budget|finance|expense|invoice|tax", "💰"), (r"news|digest|brief", "📰"), (r"research|study|learn|read", "📚"),
    (r"code|ship|github|deploy|bug|dev", "💻"), (r"fit|gym|workout|run|health", "💪"), (r"food|recipe|meal|cook", "🍳"),
    (r"travel|trip|flight|hotel", "✈️"), (r"shop|price|deal|buy", "🛍️"), (r"write|blog|story|draft", "✍️"),
    (r"sales|lead|customer|crm|outreach", "🤝"), (r"support|help ?desk|ticket", "🛟"), (r"music|song|playlist", "🎵"),
    (r"home|house|garden", "🏡"), (r"job|career|hiring|recruit", "💼"),
]


def guess_emoji(name: str, blurb: str = "") -> str:
    text = f"{name} {blurb}".lower()
    return next((e for pattern, e in EMOJI_GUESSES if re.search(pattern, text)), "")


def one_emoji(value) -> str:
    """A picture someone typed: a short run with no letters or digits (one emoji, flags and skin tones included)."""
    v = str(value or "").strip()
    if not v or len(v) > 8 or any(ch.isalnum() for ch in v) or not any(ord(ch) > 0x2000 for ch in v):
        raise ValueError("Pick one emoji.")
    return v


def picture(meta: dict) -> str:
    """The emoji an agent shows: its own when it's a real one, else a guess (maybe none)."""
    try:
        return one_emoji(meta.get("emoji"))
    except ValueError:
        return guess_emoji(meta.get("name", ""), meta.get("blurb", ""))


COLORS = ["#1F4E79", "#7A4E8C", "#5E7A3A", "#9C4A1A", "#2E6F73", "#8A3B5C", "#4A4F8C", "#6B5B2E"]
GREET_AFTER = 3 * 3600  # speak first when a chat is opened after this long
# Ken's very first contact, the same wherever it happens (the app or the phone): Ken writes it,
# from what it knows, following the awakening in its instructions.
def app_learn_prompt(name: str) -> str:
    """They just connected an app outside any chat: Ken looks through it once and keeps what it learns about them."""
    return (f"(They've just connected {name}. Nobody asked for anything: look through it once to learn about them: "
            "their profile, and a handful of their recent posts, messages or events, whatever it holds. Keep it to a few "
            "app actions. File what helps you work for them (what they do, their niche, audience, voice and style, the "
            "people and work that matter) in a topic file in your memory folder, extending one that fits. Never post, "
            "send, change or delete anything. Don't message them. When done, answer only NOTHING_TO_SAY.)")


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
# Work runs as long as it needs; only a brain that has said nothing for this long is stopped.
IDLE_LIMIT = 45 * 60
# ...but an AI that hasn't answered at all two minutes after it was asked isn't coming: stop and say so.
NO_ANSWER_LIMIT = 2 * 60
DEFAULT_WINDOW = 200_000  # tokens a model takes at once, when the AI doesn't say
# A conversation past this many tokens starts a fresh session (what mattered goes to memory, the last few messages
# come along): every step re-reads the whole context, so a long thread costs more each message. Half a small
# model's window, if that's less.
ROLL_OVER_TOKENS = 50_000
# Ken AI credits run on Haiku only (about 14x cheaper than Sonnet, and it did every real task in testing). Ken picks it;
# there's no menu on credits. The site enforces it too.
CREDIT_MODELS = ("claude-haiku-5-5",)
# Background jobs start when the computer has room for them (usage.room_for_more, looked at again every few
# seconds while one waits), and stop after an hour at most each.
ROOM_CHECK = 5
RUN_LIMIT = 60 * 60


def plain_text(text: str) -> str:
    """Ken's markdown as plain words, for a one-line preview (the chat list): no **, `, # or [label](link)."""
    lines = [re.sub(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", "", re.sub(r"^\s*[-*•]\s+", "• ", re.sub(r"^\s*#+\s*", "", line)))
             for line in text.split("\n")]
    s = re.sub(r"!?\[([^\]]+)\]\([^)\s]+\)", r"\1", "\n".join(lines))
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = re.sub(r"(^|[^\w*])\*(?!\s)([^*\n]+?)\*(?!\w)", r"\1\2", s)
    s = re.sub(r"`+([^`]*)`+", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    return re.sub(r"\n{2,}", "\n", s).strip()



def chat_transcript(home: Path, agent: str, count: int = 40) -> str:
    """An agent's recent chat as plain lines, for Ken (`--chat <agent>`): read-only, never changes it."""
    want = agent.strip().lower()
    names = {}
    for d in sorted((home / "agents").glob("*/agent.json")):
        try:
            names[d.parent.name] = json.loads(d.read_text()).get("name") or d.parent.name
        except (OSError, ValueError):
            names[d.parent.name] = d.parent.name
    pid = next((p for p, n in names.items() if want in (p, n.lower(), slugify(n))), None)
    if pid is None:
        return f"No agent called {agent!r}. Their agents: " + (", ".join(f"{n} ({p})" for p, n in names.items()) or "none") + "."
    db = sqlite3.connect(f"file:{home / 'app.db'}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT ts, data FROM (SELECT id, ts, data FROM events WHERE project=? AND type='message' "
                          "ORDER BY id DESC LIMIT ?) ORDER BY id", (pid, max(1, min(count, 200)))).fetchall()
    finally:
        db.close()
    lines = []
    for ts, data in rows:
        m = json.loads(data)
        who = names[pid] if m.get("role") == "ken" else "Ken (passing on)" if m.get("via") == "ken" else "Them"
        said = (m.get("text") or "").strip() or ("📎 " + ", ".join(m.get("files") or []))
        lines.append(f"[{time.strftime('%a %d %b %H:%M', time.localtime(ts))}] {who}: {said[:4000]}")
    return f"{names[pid]}'s chat, last {len(lines)} messages:\n" + "\n".join(lines) if lines else f"{names[pid]}'s chat is empty."

class NoAnswer(TimeoutError):
    """The AI said nothing at all for NO_ANSWER_LIMIT seconds after it was asked."""


class _Switched(Exception):
    """Their own plan hit its limit and Ken switched to Ken AI credits (they turned that on): the line that says so."""


# Words from Claude Code, the SDKs and the AI services that must never reach a person as they are.
AI_WORDS = ("api", "login", "auth", "unauthorized", "forbidden", "overloaded", "rate limit", "usage limit", "billing",
            "credit", "anthropic", "openai", "claude code", "codex", "connection", "network", "timed out", "timeout")


def explain_failure(e: BaseException, brain: str) -> tuple[str, str]:
    """A failed reply in one plain sentence, and the one thing to do about it: "retry" (Try again), "credits" (Ken AI
    credits ran out: connect their own Claude or ChatGPT, or pick a plan), "connect" (connect their own Claude or
    ChatGPT) or "reconnect" (their own account stopped working). Never the raw error:
    no Claude Code or SDK wording, status codes or model ids. The real error goes to the log."""
    from onboarding import included_ai_in_use

    name = "ChatGPT" if brain.lower() == "codex" else "Claude"
    included = included_ai_in_use() and brain.lower() == "claude"
    text = str(e)
    try:
        detail = json.loads(text[text.index("{"):])
        text = detail.get("error", {}).get("message") or detail.get("message") or text
    except (ValueError, AttributeError):
        pass
    low = text.lower()
    status = e.status if isinstance(e, AIUnavailable) else None
    codes = set(re.findall(r"\b([45]\d\d)\b", low))
    has = lambda *words: any(w in low for w in words)
    ai = isinstance(e, (AIUnavailable, NoAnswer)) or bool(codes) or has(*AI_WORDS)

    if included:
        if status == 402 or "402" in codes or has("ken ai credits", "included ai", "billing", "credit balance"):
            return "You've used your Ken AI credits.", "credits"
        if ai:  # refused, down, unreachable or silent: none of it is theirs to fix
            return "Ken couldn't reach its AI just now.", "retry"
        return "Something went wrong on Ken's side.", "retry"
    if isinstance(e, NoAnswer):
        return f"{name} didn't answer, so I stopped waiting.", "retry"
    if has("model") and has("not supported", "not found", "does not exist", "not available"):
        return f"{name} can't use that model with your account. Pick another model at the top.", "retry"
    if status == 429 or "429" in codes or has("usage limit", "rate limit", "rate_limit", "hit your limit", "limit reached"):
        return f"You've reached your {name} plan's limit for now.", "retry"
    if status == 402 or "402" in codes or has("credit balance", "billing"):
        return f"Your {name} account is out of credit.", "reconnect"
    if status in (401, 403) or codes & {"401", "403"} or has("not logged in", "/login", "unauthorized", "authentication",
                                                              "invalid api key", "invalid x-api-key", "expired", "oauth"):
        return f"Your {name} connection stopped working.", "reconnect"
    if ai:
        return f"{name} isn't answering just now.", "retry"
    return "Something went wrong on Ken's side.", "retry"


def carry_on(name: str, done: list[str] | None = None) -> str:
    """The note for the one turn retried on Ken AI credits after their own plan hit its limit partway through.
    `done`: what it had already said or done, when the new session can't see it (another runtime)."""
    did = ("\nWhat you'd already said or done:\n" + "\n".join(f"- {d[:300]}" for d in done[-20:])) if done else ""
    return (f"(Their {name} plan hit its limit partway through this, so you're on Ken AI credits now. Carry on from "
            f"where you stopped. Don't redo or send again anything you already did.){did}")


async def _note(done: list[str], t: str, then) -> None:
    """Keep what a run said or did (for carrying on after a switch), then pass it on."""
    done.append(t)
    await then(t)


def auto_credits() -> bool:
    """Settings → AI: keep going on Ken AI credits when their own plan hits its limit (off unless they turn it on)."""
    return os.environ.get("KEN_AUTO_CREDITS") == "1"


def settings_ai(via: str | None = None) -> str:
    """Where they switch AI: Settings → AI, which from Telegram is on the site."""
    return "Settings → AI on kencomputer.dev" if via == "telegram" else "Settings → AI"


def friendly_error(e: BaseException, brain: str) -> str:
    """What to tell the person when a reply fails (see explain_failure)."""
    return explain_failure(e, brain)[0]


def kill_children() -> None:
    """Kill every process this one started, and theirs (AI sessions, their commands, browsers), at once. Where there's
    no `ps` (Windows), nothing: closing the sessions stops them there."""
    import signal
    kids: dict[int, list[int]] = {}
    for line in usage._run("ps", "-A", "-o", "pid=", "-o", "ppid=").splitlines():
        parts = line.split()
        if len(parts) == 2 and all(x.isdigit() for x in parts):
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, todo = [], list(kids.get(os.getpid(), []))
    while todo:
        pid = todo.pop()
        found.append(pid)
        todo += kids.get(pid, [])
    for pid in found:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


async def until_idle(session, work: Awaitable, limit: float = IDLE_LIMIT, first: float | None = None):
    """Await the brain's work, raising TimeoutError once it has been silent for `limit` seconds,
    and NoAnswer when the AI hasn't said anything at all `first` seconds after it was asked."""
    first = NO_ANSWER_LIMIT if first is None else first
    task, started = asyncio.ensure_future(work), time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=min(5, limit, first))
            if done:
                return task.result()
            if not getattr(session, "heard", True) and time.monotonic() - started > first:
                raise NoAnswer
            if time.monotonic() - (getattr(session, "last_activity", 0) or time.monotonic()) > limit:
                raise asyncio.TimeoutError
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
BUILTIN_TOOLS = ("browser", "email", "calendar")


def link_dir(link: Path, target: Path) -> None:
    """A folder that shows another folder: a relative symlink, or a junction on Windows (no admin needed)."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)


# Skills the person made in their other AI apps. Ken reads them where they are and never writes there.
OTHER_SKILLS = {
    "Claude Code": [Path.home() / ".claude" / "skills"],
    "Codex": [Path.home() / ".agents" / "skills", Path.home() / ".codex" / "skills"],
}


def _skill_folders(d: Path) -> list[Path]:
    try:
        return sorted(f for f in d.iterdir() if not f.name.startswith(".") and (f / "SKILL.md").is_file())
    except OSError:
        return []


def other_skills(others: dict[str, list[Path]]) -> list[tuple[str, str, Path]]:
    """(id, app, folder) for each skill in the other apps' folders, like "claude-code:pdf"."""
    out, seen = [], set()
    for app, dirs in others.items():
        for d in dirs:
            for f in _skill_folders(d):
                sid = f"{slugify(app)}:{f.name}"
                if sid not in seen:
                    seen.add(sid)
                    out.append((sid, app, f))
    return out


def skills_off(home: Path) -> set[str]:
    try:
        return set(json.loads((home / "skills-off.json").read_text()))
    except (OSError, ValueError, TypeError):
        return set()


def _unlink(p: Path) -> None:
    """Remove a link (a symlink, or a junction on Windows), never what it points at."""
    try:
        p.unlink()
    except OSError:
        os.rmdir(p)


# Ken's own skills that the host switches on per person (kencomputer.dev: VIDEO_KIT_ROLLOUT, TREG_ROLLOUT). Off, Ken
# doesn't have them at all: not linked, so it can't see, use or mention them. The host says which are on in every
# check-in answer ("kits"), kept in <home>/kits-on.json (save_kits). The video kit also runs on their own ElevenLabs
# key when they've added one (their account, their choice).
HOST_KITS = ("video-kit", "live-data")


def kits_on(home: Path) -> dict:
    try:
        d = json.loads((home / "kits-on.json").read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def kit_on(home: Path, name: str) -> bool:
    if kits_on(home).get(name) is True:
        return True
    return name == "video-kit" and bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def save_kits(home: Path, kits) -> bool:
    """The host's word on which kits are on, from a check-in answer. True when it changed (the skills are gathered
    again). An answer without it (an older host) changes nothing."""
    if not isinstance(kits, dict):
        return False
    now = {k: kits.get(k) is True for k in HOST_KITS}
    if kits_on(home) == now:
        return False
    try:
        (home / "kits-on.json").write_text(json.dumps(now))
    except OSError:
        return False
    return True


def gather_skills(home: Path, others: dict[str, list[Path]]) -> Path:
    """<home>/.skills-in-use: a link to each skill the brains should see. Ken's own skills, then the
    ones from the person's other AI apps that aren't switched off. A name Ken already has stays Ken's."""
    use = home / ".skills-in-use"
    use.mkdir(parents=True, exist_ok=True)
    want = {f.name: f for f in _skill_folders(home / "skills") if f.name not in HOST_KITS or kit_on(home, f.name)}
    off = skills_off(home)
    for sid, _, f in other_skills(others):
        if sid not in off and f.name not in want:
            want[f.name] = f
    for link in use.iterdir():
        if link.name not in want or os.path.realpath(link) != os.path.realpath(want[link.name]):
            _unlink(link)
    for name, f in want.items():
        if not os.path.lexists(use / name):
            link_dir(use / name, f)
    return use


def prepare_skills(home: Path, builtin: Path | None = None, others: dict[str, list[Path]] | None = None) -> Path | None:
    """Ken's skills live in <home>/skills, one folder per skill (SKILL.md and its files), shared by
    every chat and both brains, along with the skills from the person's other AI apps (`others`).
    Claude Code only finds skills inside a plugin, so a small hidden plugin, <home>/.skills-plugin,
    points at <home>/.skills-in-use. Returns the plugin, or None if it can't be made.
    The skills Ken ships with (`builtin`) are copied in and kept up to date, unless the copy lost its
    .from-ken mark (someone made it their own)."""
    skills, plugin = home / "skills", home / ".skills-plugin"
    try:
        skills.mkdir(parents=True, exist_ok=True)
        for src in sorted(builtin.iterdir()) if builtin and builtin.is_dir() else []:
            dest = skills / src.name
            removed = skills / f".removed-{src.name}"  # they removed it in Brain: it stays gone
            if src.is_dir() and not removed.exists() and (not dest.exists() or (dest / ".from-ken").exists()):
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(src, dest)
                (dest / ".from-ken").write_text("Ships with Ken and updates with it. Delete this file to keep your own changes.\n")
        manifest = plugin / ".claude-plugin" / "plugin.json"
        if not manifest.exists():
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({"name": "ken-skills", "description": "Skills Ken has added"}, indent=2) + "\n")
        use = gather_skills(home, others or {})
        if os.path.lexists(plugin / "skills") and os.path.realpath(plugin / "skills") != os.path.realpath(use):
            _unlink(plugin / "skills")  # older Ken pointed it at <home>/skills
        if not os.path.lexists(plugin / "skills"):
            link_dir(plugin / "skills", use)
        return plugin
    except OSError as e:
        log.warning("couldn't set up the skills folder: %s", e)
        return None


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "project"


# Things never said out loud or sent to a voice service: keys and tokens, and paths on this computer.
SECRET_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|sk_(?:live|test)_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|"
                       r"AKIA[0-9A-Z]{16}|\d{6,}:[A-Za-z0-9_-]{30,}|[A-Za-z0-9+/_=-]{40,})")
PATH_TEXT_RE = re.compile(r"(?<![\w/])(?:~|/(?:home|Users|root|tmp|var|opt|etc|mnt|private))/[^\s'\"`)>\]]*")


def speakable(text: str) -> str:
    text = re.sub(r"(?im)^.*\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\b\s*[=:].*$", "", str(text))
    return PATH_TEXT_RE.sub("(a file)", SECRET_RE.sub("(hidden)", text))


def safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f]", "", name).lstrip(".")
    return name[:180]


# What Ken keeps in an agent's folder to run it, as against the files it made: removing an agent deletes these.
AGENT_BOOKKEEPING = {"agent.json", "project.json", "CONTEXT.md", "jobs.json", "tools.json", "autonomy.json", "memory"}
# Its settings in app.db's kv, each "<prefix>:<agent id>".
AGENT_KV = ("backend", "brain", "session", "session:codex", "thread", "primer", "rolled", "jobs:said", "onboarding:said", "voice:said")
REMOVED_CHATS = "Removed chats"  # in Ken's own Files: a removed agent's files, when their Library isn't on


def _unique(p: Path) -> Path:
    stem, suffix, n = p.stem, p.suffix, 2
    while p.exists() or p.is_symlink():
        p, n = p.with_name(f"{stem} {n}{suffix}"), n + 1
    return p


def _codex_cwd(rollout: Path) -> str:
    """The folder a Codex conversation ran in, from the first line of its session file."""
    with open(rollout, encoding="utf-8", errors="replace") as f:
        line = f.readline(1 << 20)
    try:
        return str(json.loads(line).get("payload", {}).get("cwd") or "")
    except (ValueError, AttributeError):
        return ""


def _forget_sessions(pid: str, real_dir: str, home: Path, sids: list[str], codex: bool) -> None:
    """A removed agent's conversations as the AI keeps them: Claude Code's folder for its path, and Codex's session
    files that ran there or carry its session ids. Never another folder's."""
    enc = lambda p: re.sub(r"[^A-Za-z0-9]", "-", str(p))  # noqa: E731 — how Claude Code names them
    store = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    for cwd in {real_dir, os.path.realpath(home) + f"/projects/{pid}"}:
        shutil.rmtree(store / enc(cwd), ignore_errors=True)
    sessions = Path(os.environ.get("KEN_CODEX_HOME") or Path.home() / ".codex") / "sessions"
    if codex and sessions.is_dir():
        for f in sessions.rglob("rollout-*.jsonl"):
            try:
                if any(f.name.endswith(f"-{s}.jsonl") for s in sids) or _codex_cwd(f) == real_dir:
                    f.unlink()
            except OSError:
                continue


def _library_on() -> bool:
    import library
    return library.on()


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



# Dates without a leading zero ("March 5", "9:05 AM") on every platform: "%-d" works on Mac and Linux but not Windows.
def _short_date(t=None) -> str:
    t = t or time.localtime()
    return f"{time.strftime('%b', t)} {t.tm_mday}"


def _day_and_time(t=None) -> str:
    t = t or time.localtime()
    hour = t.tm_hour % 12 or 12
    return f"{time.strftime('%A, %B', t)} {t.tm_mday}, {hour}:{time.strftime('%M %p', t)}"

def _temp_roots() -> list[Path]:
    """The temp folders Ken may save a quick file in: the system's, and /tmp where there is one."""
    import tempfile
    roots = {Path(tempfile.gettempdir()).resolve()}
    if Path("/tmp").is_dir():
        roots.add(Path("/tmp").resolve())
    return list(roots)


def _owned(p: Path) -> bool:
    """Made by this user (Ken runs as them), not someone else's file in a shared temp folder."""
    getuid = getattr(os, "getuid", None)
    return getuid is None or p.stat().st_uid == getuid()


def clean_library(items) -> list[dict]:
    """Library items on a message (theirs, or ones Ken shows): ids and enough to describe them, nothing else."""
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and re.fullmatch(r"[0-9a-f]{16}", str(it.get("id", ""))):
            out.append({"id": it["id"], "kind": it.get("kind") if it.get("kind") in ("photo", "video", "file") else "photo",
                        "name": str(it.get("name") or "")[:120], "place": str(it.get("place") or "")[:80]})
    return out[:50]


def library_note(items: list[dict]) -> str:
    """How Ken hears about Library items they attached: what each is and its id, to fetch with the Library commands."""
    said = "; ".join(f"{it['kind']}{' from ' + it['place'] if it.get('place') else ''} (id {it['id']})" for it in items)
    return f"They attached {len(items)} from their Library: {said}. Get them with the Library commands."


class Engine:
    def __init__(self, home: Path, brain: Brain, *, brains: dict[str, Brain] | None = None) -> None:
        self.home = Path(home)
        self.brain = brain
        self.brains = brains or {brain.name: brain}
        self.model_choices = None  # async backend -> available model IDs
        self.plan_limits = None  # () -> {"claude": [window...], "codex": [...]}: Settings → AI's meters
        self.limit_resets = None  # backend -> "15:00" when a plan that hit its limit opens again, or ""
        self.forget_dead_claude = None  # async () -> bool: forget a Claude sign-in Anthropic no longer accepts (bot.py)
        self.usable_brains = None  # () -> the brains Ken can run on now (their own accounts, or Claude on Ken AI credits)
        self.resume_listener = None  # via -> where a resumed reply also goes (Telegram), or None
        self.stopping = False  # set while Ken shuts down (close)
        self.credits_model = None  # backend -> the model Ken AI credits run on ("" when that backend isn't on them)
        self.agents_dir = self.home / "agents"
        self.requests_dir = self.home / "requests"
        self.db = sqlite3.connect(self.home / "app.db", isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.subscribers: set[asyncio.Queue] = set()
        self.chats: dict[str, object] = {}
        self.chat_locks: dict[str, asyncio.Lock] = {}
        self.chat_tasks: set[asyncio.Task] = set()
        self.chat_tools: dict[str, float] = {}  # when each open agent chat last read its tools.json
        self.home_cwd = self.home / "work"
        self.other_skills: dict[str, list[Path]] = {}  # app name -> its skills folders (OTHER_SKILLS in Ken)
        self.home_system: Callable[[], str] = lambda: ""
        self.agent_base: Callable[[], str] = lambda: ""
        # The built-in tools ("browser", "email", "calendar") an agent may be given: MCP servers, and their prompt.
        self.tools: Callable[[set, str], dict] = lambda only, backend: {}
        self.tools_prompt: Callable[[set], str] = lambda only: ""
        self.history_hook: Callable[[str, str], None] | None = None
        self.new_install: Callable[[], bool] = lambda: False  # true until onboarding has begun
        self._greeting: set[str] = set()
        self._booted = time.time()  # turns started after this belong to this run, never "cut off by a restart"
        self.run_tasks: dict[str, asyncio.Task] = {}
        self.run_sessions: dict[str, object] = {}
        self._aside: dict[str, list[str]] = {}  # answered on the side while the chat was busy, for the chat to hear next
        self._last_start = 0.0  # when the last background job started (monotonic)
        self._bg: list[asyncio.Task] = []
        # The phone message behind the turn in progress, and behind each run/approval it started, so
        # their results can quote it in Telegram instead of whatever the human said last.
        self.chat_origin: int | None = None
        self.origins: dict[str, int] = {}

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
        for r in self.db.execute("SELECT title FROM runs WHERE project=? AND status IN ('running','queued')", (HOME,)).fetchall():
            self.log_message(HOME, "ken", f'■ Ken restarted, which stopped the background job "{r["title"]}".', via="background")
        self.db.execute(
            "UPDATE runs SET status='stopped', finished=?, summary=COALESCE(summary,'Interrupted by a restart.') "
            "WHERE status IN ('running','queued')", (now,),
        )
        self._bg.append(asyncio.create_task(self._watch_requests()))
        from workspace import keep_tidy  # old uploads, raw videos and renders go by themselves (workspace.py)
        self._bg.append(asyncio.create_task(keep_tidy(self.home)))
        removing = self.home / "removing"  # a removed agent's files a restart stopped on their way to the Library
        for staging in sorted(removing.iterdir()) if removing.is_dir() else []:
            if staging.is_dir() and not staging.is_symlink():
                self._bg.append(asyncio.create_task(self._send_to_library(staging)))

    async def close(self) -> None:
        """Stop what's running before the process exits so tools are not orphaned. A chat reply stopped this way keeps
        its turn-in-progress note, so the next start carries on with it."""
        self.stopping = True
        pending = set(self._bg) | set(self.run_tasks.values()) | self.chat_tasks
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*(s.close() for s in self.chats.values()), return_exceptions=True)

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

    def retry_last(self, pid: str) -> Awaitable[bool] | None:
        """Ask again after a reply that failed: the message they sent before it, without logging it twice.
        None when there's nothing to retry (Ken is busy, or the last reply didn't fail)."""
        recent = self.messages(pid, limit=40)
        if self.chat_busy(pid) or not recent or not (recent[-1].get("error") and recent[-1].get("action")):
            return None
        asked = next((m for m in reversed(recent) if m["role"] == "you"), None)
        if asked is None:
            return None
        return self.send_message(pid, asked.get("text", ""), asked.get("files") or [], log_user=False, via="app")

    def credits_low(self, used_pct: int) -> dict:
        """Hosted Ken: its host says most of the Ken AI credits are used. One note in Home's chat, with the three ways on
        (the web app shows them as buttons): their own Claude, their own ChatGPT, or a plan with more credits."""
        text = (f"You've used {used_pct}% of your Ken AI credits. To keep going without a break, connect your own Claude "
                "or ChatGPT, or pick a plan with more Ken AI credits.")
        return self.log_message(HOME, "ken", text, action="credits", note=True)

    def apps_reconnect(self, note_id: str, apps: list[dict]) -> dict:
        """Hosted Ken: its host moved their apps to Ken's own Composio, so the apps they had need connecting again. One
        message in Home's chat with a one-tap Connect link for each; it reaches the app, the phone and Telegram like any
        message Ken starts. Said once per `note_id`, however often the host asks."""
        for m in reversed(self.messages(HOME, limit=200)):
            if m.get("note_id") == note_id:
                return m
        links = " · ".join(f"[{a['name']}]({a['url']})" for a in apps)
        text = ("I've upgraded how I connect to your apps. Reconnect them once and everything works as before. "
                f"Tap each one: {links}")
        return self.log_message(HOME, "ken", text, via="background", action="apps_reconnect", note_id=note_id)

    async def app_connected(self, slug: str, name: str) -> str | None:
        """Hosted Ken: they just connected an app. The chat that gave the link (the most recent Ken reply with it, in
        the last hour) carries on with what they asked; returns that chat. Connected anywhere else, Ken looks through
        it once on its own and keeps what it learns about them in memory, so it never asks what the app already shows."""
        link = f"/account/apps/connect/{slug}"
        since = time.time() - 3600
        best = None
        for pid in [HOME, *[a["id"] for a in self.list_agents()]]:
            for m in reversed(self.messages(pid, limit=20)):
                # Not the note asking them to reconnect after the move (apps_reconnect): no chat asked for anything.
                if m["role"] != "you" and link in (m.get("text") or "") and m["ts"] >= since and m.get("action") != "apps_reconnect":
                    if best is None or m["ts"] > best[1]:
                        best = (pid, m["ts"])
                    break
        if best is None:
            # Connected from Settings or the welcome, not for a task: learn what it shows about them, once, quietly.
            self._bg.append(asyncio.create_task(self.run_job(HOME, app_learn_prompt(name), job={"name": "app-learn"}, silent=True)))
            return None
        pid = best[0]
        self._bg.append(asyncio.create_task(self.send_message(
            pid, "", log_user=False, via="app", steer=True, prompt=f"(They've just connected {name}. Carry on with what they asked.)")))
        return pid

    async def first_contact(self) -> None:
        """A brand-new install's Home chat was opened: Ken speaks first, exactly as it does on Telegram."""
        # Not put off by a busy chat: right after connecting, the app warms the session (which holds the chat for a
        # moment), and giving up then meant Ken never spoke first. send_message waits its turn instead.
        if not self.new_install() or HOME in self._greeting or self.db.execute(
                "SELECT 1 FROM events WHERE project=? AND type='message' LIMIT 1", (HOME,)).fetchone():
            return
        # A hello that just failed isn't tried again for 10 minutes (each page load would otherwise retry it at once).
        if time.time() - getattr(self, "_hello_failed_at", 0) < 600:
            return
        self._greeting.add(HOME)
        try:
            ok = await self.send_message(HOME, "", log_user=False, via="app",
                                         prompt=first_contact_prompt("opened the Ken app on this computer for the first time"))
            if ok is False:
                self._hello_failed_at = time.time()
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

    def _agent_metadata(self, pid: str) -> dict:
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

    def get_agent(self, pid: str) -> dict:
        meta = self._agent_metadata(pid)
        meta["backend"] = self.backend(pid)
        if meta["backend"] == "codex":
            meta["model"] = meta.get("model_codex", "")
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

    def create_agent(self, name: str, path: str = "", context: str = "", blurb: str = "", backend=None) -> dict:
        if backend is not None and (not isinstance(backend, str) or backend not in self.brains):
            raise ValueError("Choose Claude or Codex.")
        name = name.strip()[:60] or ("New agent" if context.strip() else "")  # it names itself as it sets up
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
        if len(self.brains) > 1:
            self.kv_set(f"backend:{pid}", backend or self.backend(HOME))
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
        meta = self._agent_metadata(pid)
        path = fields.get("path")
        if path and not Path(os.path.expanduser(str(path).strip())).is_dir():
            raise ValueError(f"folder not found: {path}")
        if fields.get("name") is not None and not str(fields["name"]).strip():
            raise ValueError("an agent needs a name")
        if fields.get("emoji") is not None:
            meta["emoji"] = one_emoji(fields["emoji"])
        for k in ("name", "blurb", "path", "model"):
            if fields.get(k) is not None:
                key = "model_codex" if k == "model" and self.backend(pid) == "codex" else k
                meta[key] = str(fields[k]).strip()[:60 if k == "name" else 500]
        if path is not None:
            self.chats.pop(pid, None)  # the next message gets the new folder in its prompt
        meta.pop("id", None)
        meta.pop("workdir", None)
        (self._adir(pid) / "agent.json").write_text(json.dumps(meta, indent=1) + "\n")
        self.emit("project.updated", project=pid)
        return self.get_agent(pid)

    async def remove_agent(self, pid: str, delete_files: bool = False) -> dict:
        """Remove an agent for good: its chat, notes, memory, keys, settings and jobs are deleted. The files it made
        are kept unless `delete_files`: into their Library when it's on (uploaded in the background), else into
        Removed chats/<name>/ in Ken's own Files. The code or info folder it was pointed at is never touched."""
        def refuse_if_busy():
            if self.chat_busy(pid) or self.db.execute(
                    "SELECT 1 FROM runs WHERE project=? AND status IN ('running','queued') LIMIT 1", (pid,)).fetchone():
                raise ValueError("This agent is working. Stop it first.")

        meta = self.get_agent(pid)
        refuse_if_busy()
        d = self._adir(pid)
        code = self.code_dir(meta)
        if code is not None and (code.resolve() == d.resolve() or d.resolve() in code.resolve().parents):
            raise ValueError("Its code folder is inside its own folder. Point it at another folder first.")
        to_library = not delete_files and bool(self.agent_files(pid)) and await asyncio.to_thread(_library_on)
        s = self.chats.pop(pid, None)
        if s is not None:
            await s.close()
        refuse_if_busy()  # nothing may start between here and deleting it: no more waiting from here on
        files = [] if delete_files else self.agent_files(pid)
        to_library = to_library and bool(files)
        where = None
        if files:
            if to_library:  # held here until each one is safely in their Library
                where = self.home / "removing" / f"{pid}-{int(time.time() * 1000)}"
                (where / "files").mkdir(parents=True)
                (where / "chat.json").write_text(json.dumps({"name": meta["name"]}))
                into = where / "files"
            else:
                where = into = self._removed_folder(meta["name"])
            moved = []
            for path, rel in files:
                try:
                    dest = _unique(into / rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(dest))
                    moved.append((path, dest))
                except OSError as e:  # never delete a file that was meant to be kept: put them back, the agent stays
                    log.warning("couldn't keep %s from %s: %s", rel, pid, e)
                    for back, there in reversed(moved):
                        with contextlib.suppress(OSError):
                            shutil.move(str(there), str(back))
                    shutil.rmtree(where, ignore_errors=True)
                    raise ValueError(f"Couldn't move its file {rel}, so nothing was removed. Try again.") from None
        real = os.path.realpath(d)
        shutil.rmtree(d, ignore_errors=True)  # links inside go as links; what they point to is never touched
        self.emit("project.removed", project=pid)
        sids, codex = self._forget_chat(pid)
        await asyncio.to_thread(_forget_sessions, pid, real, self.home, sids, codex)
        if to_library and where is not None:
            self._bg.append(asyncio.create_task(self._send_to_library(where)))
        out = {"files": len(files), "to": ("library" if to_library else "folder") if files else ("deleted" if delete_files else None)}
        if files and not to_library:
            out["folder"] = str(where)
        return out

    def agent_files(self, pid: str) -> list[tuple[Path, str]]:
        """The files an agent made (or was sent): everything in its folder but Ken's bookkeeping (who it is, its
        memory, jobs, tools and rules) and hidden entries (unfinished uploads, Codex's skills link). Links are never
        followed. Each with where it goes: its path in the folder, without the leading files/."""
        d = self._adir(pid)
        out = []
        for dirpath, dirnames, filenames in os.walk(d, followlinks=False):
            here = Path(dirpath)
            top = here == d
            dirnames[:] = [n for n in dirnames if not n.startswith(".") and not (top and n in AGENT_BOOKKEEPING)]
            for n in filenames:
                p = here / n
                if n.startswith(".") or (top and n in AGENT_BOOKKEEPING) or p.is_symlink() or not p.is_file():
                    continue
                parts = p.relative_to(d).parts
                out.append((p, "/".join(parts[1:] if parts[0] == "files" and len(parts) > 1 else parts)))
        return sorted(out, key=lambda x: x[1])

    def agent_files_size(self, pid: str) -> dict:
        files = self.agent_files(pid)
        return {"count": len(files), "bytes": sum(p.lstat().st_size for p, _ in files)}

    def _removed_folder(self, name: str) -> Path:
        """Removed chats/<name>/ in Ken's own Files (a new one if a chat of that name was removed before)."""
        base = self.files_dir(HOME) / REMOVED_CHATS
        clean = safe_filename(name) or "Chat"
        d, n = base / clean, 2
        while d.exists():
            d, n = base / f"{clean} {n}", n + 1
        d.mkdir(parents=True)
        return d

    def _forget_chat(self, pid: str) -> tuple[list[str], bool]:
        """Everything Ken kept about a removed agent outside its folder: its messages, its jobs' steps, approvals,
        settings and keys. Returns its AI session ids, and whether it used Codex, for _forget_sessions."""
        sids = [s for s in (self.kv_get(f"session:{pid}"), self.kv_get(f"session:codex:{pid}")) if s]
        codex = bool(self.kv_get(f"session:codex:{pid}")) or "codex" in (self.kv_get(f"backend:{pid}"), self.kv_get(f"brain:{pid}"))
        runs = [r["id"] for r in self.db.execute("SELECT id FROM runs WHERE project=?", (pid,)).fetchall()]
        self.db.execute("DELETE FROM events WHERE project=?", (pid,))
        self.db.executemany("DELETE FROM events WHERE run=?", [(r,) for r in runs])
        self.db.execute("DELETE FROM runs WHERE project=?", (pid,))
        self.db.execute("DELETE FROM approvals WHERE project=?", (pid,))
        keys = [f"{k}:{pid}" for k in AGENT_KV] + [f"spent:{s}" for s in sids]
        self.db.executemany("DELETE FROM kv WHERE k=?", [(k,) for k in keys])
        shutil.rmtree(self.home / "credentials" / "agents" / pid, ignore_errors=True)
        for m in (self.chat_locks, self.chat_tools, self._aside, self.origins):
            m.pop(pid, None)
        self._turn_ended(pid)
        return sids, codex

    async def _send_to_library(self, staging: Path) -> None:
        """A removed agent's files, into their Library one by one; each local copy goes once it's safely there.
        Whatever couldn't go lands in Removed chats/<name>/ instead. Picked up again after a restart."""
        import library
        try:
            name = json.loads((staging / "chat.json").read_text()).get("name") or "Chat"
        except (OSError, ValueError):
            name = "Chat"
        folder = None
        root = staging / "files"
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()) if root.is_dir() else []:
            try:
                await asyncio.to_thread(library.add, path)
                path.unlink(missing_ok=True)
                continue
            except Exception as e:
                log.warning("couldn't add %s to the Library: %s", path.name, e)
            try:
                folder = folder or self._removed_folder(name)
                dest = _unique(folder / path.relative_to(root))
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
            except OSError as e:
                log.warning("couldn't keep %s: %s", path.name, e)
        if not root.is_dir() or not any(p.is_file() and not p.is_symlink() for p in root.rglob("*")):
            shutil.rmtree(staging, ignore_errors=True)  # else what's left is tried again on the next start

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

    def _latest_steps(self, rid: str, n: int = 3) -> str:
        """What a background job did last, newest first, so asked how it's going, Ken answers from what the job is
        actually doing (the same steps the app shows) rather than guessing."""
        rows = self.db.execute("SELECT ts, data FROM events WHERE run=? AND type IN ('run.text', 'run.tool') ORDER BY id DESC LIMIT ?",
                               (rid, n)).fetchall()
        steps = []
        for ts, data in rows:
            try:
                text = " ".join(str(json.loads(data).get("text", "")).split())[:120]
            except (ValueError, AttributeError):
                continue
            if text:
                steps.append(f'{int((time.time() - ts) // 60)}m ago: {text}')
        return "; latest steps: " + " | ".join(steps) if steps else ""

    def shared_file(self, ref: str) -> Path:
        """A file or folder Ken named in the chat, if it's one they may open: anything in Ken's work, inbox,
        outbox or agents, in their own home outside hidden folders, or a file Ken made in the temp folder (a quick
        picture it shows, like a before/after; else the chat shows an empty box). Never Ken's keys or settings."""
        ref = ref.strip().strip("`").removeprefix("file://")
        p = Path(os.path.expanduser(ref))
        tries = [p] if p.is_absolute() else [self.home_cwd / p, self.home / p]
        roots = [self.home_cwd, self.files_dir(HOME), self.home / "outbox", self.home / "sent", self.agents_dir]  # sent: until moved
        user, ken = Path.home().resolve(), self.home.resolve()
        for t in tries:
            try:
                r = t.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if any(r.is_relative_to(root.resolve()) for root in roots if root.exists()):
                return r
            if r.is_relative_to(user) and not r.is_relative_to(ken) and not any(
                    part.startswith(".") for part in r.relative_to(user).parts):
                return r
            if r.is_file() and not r.is_relative_to(ken) and _owned(r) and any(
                    r.is_relative_to(t) and not any(part.startswith(".") for part in r.relative_to(t).parts)
                    for t in _temp_roots()):
                return r
        # A file an older message named in the outbox, kept in the workspace's sent-files since no Telegram was there to
        # send it (bot.tidy_outbox; ~/.ken/sent before that): the same file, so their old Download button still opens it.
        outbox = (self.home / "outbox").resolve()
        for t in tries:
            try:
                rel = t.absolute().relative_to(outbox) if t.is_absolute() else None
            except ValueError:
                rel = None
            if rel is None and t.is_absolute():
                try:
                    rel = t.parent.resolve().joinpath(t.name).relative_to(outbox)
                except (ValueError, OSError):
                    rel = None
            for kept in ((self.home_cwd / "sent-files" / rel), (self.home / "sent" / rel)) if rel else ():
                if kept.is_file():
                    return kept.resolve()
        raise KeyError(ref)

    async def _readable_picture(self, pid: str, name: str) -> str:
        """An iPhone photo (HEIC) as a JPG next to it, so Ken can look at it at once. The AI can't read HEIC, so
        otherwise Ken spends a step converting it (and the app says 'Editing your video'). Anything else, or a
        computer without a converter, stays as it is."""
        src = self.files_dir(pid) / name
        if src.suffix.lower() not in (".heic", ".heif") or not src.is_file():
            return name
        out = src.with_suffix(".jpg")
        if out.exists():
            return out.name
        tool = shutil.which("magick") or shutil.which("convert")
        if tool:
            cmd = [tool, str(src), "-auto-orient", "-resize", "2048x2048>", "-quality", "85", str(out)]
        elif shutil.which("sips"):  # a Mac
            cmd = ["sips", "-s", "format", "jpeg", "-Z", "2048", str(src), "--out", str(out)]
        else:
            return name
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=30)
        except (OSError, asyncio.TimeoutError):
            with contextlib.suppress(Exception):
                proc.kill()
        if out.is_file() and out.stat().st_size > 0:
            return out.name
        out.unlink(missing_ok=True)
        return name

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
        runtime = f"Runtime for this conversation: {self.backend(pid).capitalize()}."
        if pid == HOME:
            return f"{self.home_system()}\n\n{runtime}\n\n{self.kit(pid)}"
        p = self.get_agent(pid)
        d = self._adir(pid)
        auto = self.get_autonomy(pid)
        tools = self.tools_config(pid)
        r = self.requests_dir
        extra = ", ".join(tools["mcp"]) or "none yet"
        where = (f" The code and files for your job are in {p['path']}: go there when a task needs them, and leave "
                 "anything there you didn't make for this job as it is.") if p.get("path") else ""
        return (
            f"{self.agent_base()}\n\n{runtime}\n\n=== You are {p['name']}, one of their agents ===\n"
            "They set you up in Ken for one job, described in CONTEXT.md below. Ken, their main assistant, "
            "is your chief of staff and may hand you work. Stay on your job. You don't see their other agents. "
            f"What's known about them in general (who they are, how they write, their people and projects) is in Ken's "
            f"memory, {self.home / 'memory'}: read a note there when it helps, never write there. It holds:\n"
            f"{memory_listing(self.home / 'memory')}\nEverything of your own lives in your folder, {d}.\n"
            f"- CONTEXT.md: who you are, your goal, your rules (below). Read with every message; keep it current.\n"
            f"- memory/: your notes, one topic per file, its first line saying what it holds. Now:\n{memory_listing(d / 'memory')}\n"
            f"- jobs.json: your schedule, a JSON array like {{\"name\": \"daily-report\", \"time\": \"08:00\", \"days\": \"daily\", \"prompt\": \"...\"}} "
            '("days": daily, weekdays or e.g. "mon,thu"; "date": "2026-10-31" instead makes it one-time, removed after it runs; '
            '"on": false pauses one). Each job runs here, in this chat, at '
            "this computer's local time. Add, change or remove jobs when they ask.\n"
            f"- tools.json: what you may use. \"use\" picks Ken's built-in tools ({', '.join(BUILTIN_TOOLS)}); "
            "\"mcp\" adds any MCP server, in Claude Code's mcpServers format, to give yourself a new tool (it's ready "
            f"from the next message). Now: {', '.join(tools['use']) or 'no built-ins'}; extra servers: {extra}.\n"
            f"- files/: what they drop into this chat.\n"
            f"- Keys: {self._keys_line(pid)} They add keys on this agent's Brain page, under Accounts & passwords. When you "
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
        background = "" if pid != HOME else (
            "BACKGROUND JOBS: the chat must stay free. Anything that will take more than about 30 seconds (editing a video, "
            "long research, building something) goes to a background job right away, before you start on it, never here "
            "where it would keep them waiting. Before handing it off, take a quick look and tell them in a few short lines "
            "what you found, what you'll do and roughly how long it'll take (no need to wait for a yes unless something's "
            "unclear). For a long job, show them a small piece first and wait for their yes: something they only see at "
            "the end costs them the whole wait again if it's not what they wanted. Then hand it to a background job: write "
            f'{{"type": "run", "title": "a few words, maybe an emoji", "prompt": "the brief"}} into {self.requests_dir}/, '
            "then tell them in a line that it's running and they can keep talking to you. The job starts fresh and sees only "
            "the brief, so make it complete: what they want, the full paths of the files involved, and anything from this "
            "conversation that matters. When it finishes, its report comes back to you as a message; tell them the result "
            "then. As many run at once as this computer has room for, and the rest wait their turn; each stops after an hour. Running jobs are "
            f'listed in the line at the top of each message, with how long each has run and its latest steps; to stop one, write {{"type": "stop", "run": "<id>"}} there '
            "too. Quick things, and anything that needs their answers along the way, you do yourself, here.\n"
        )
        return background + (
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

    def chief_brief(self, read_chat: str = "") -> str:
        """What Ken, as chief of staff, knows about every agent. `read_chat`: the command that shows an agent's chat."""
        r = self.requests_dir
        lines = [
            "=== Their agents: you are their chief of staff ===",
            "Besides this chat they have agents: each is its own chat in the app with one job, its own notes and its "
            f"own schedule, in {self.agents_dir}/<id>/ (CONTEXT.md, memory/, jobs.json, files/). You see every agent; "
            "they each see only their own folder (and read your memory). You're the one they can always just talk to:",
            "- Asked what an agent is up to or what it found: answer from the list below; for more, "
            + (f"run `{read_chat} <id>` (add a number for more than the last 40 messages) to read its chat, and " if read_chat else "")
            + f"open its notes and files ({self.agents_dir}/<id>/).",
            f'- Asked to tell an agent something or give it work: write {{"type": "message", "agent": "<id>", "text": "what '
            f'they asked, in full"}} into {r}/. It lands in that agent\'s chat, which does the work there, and a line here '
            "that opens it says it was sent, so just confirm in a few words and don't do the work yourself.",
            '- Asked for a new agent: write {"type": "agent", "name": "...", "context": "what it is for, in their words", '
            '"path": "the code or info folder its job is about, if any"}; it opens its own chat and sets itself up there.',
            "- When they ask you for something that's clearly an agent's job, do it, then offer once in a line to have "
            "that agent take it from now on. Never move anything without their yes, and don't offer the same thing twice.",
            "- An agent's routine work stays in its own chat: don't relay it here. In the morning brief, add one line "
            "for an agent only when it finished something or is waiting on them.",
            "- Agents keep their own notes: in your nightly review, also skim each agent's memory/ and file in your own "
            "memory anything about them in general (a move, a preference, a new person) that it lacks. What's only "
            "about that agent's job stays with the agent.",
            "Their agents now:",
        ]
        running = self.list_runs(active=True)
        approvals = self.pending_approvals()
        for a in self.list_agents():
            pid = a["id"]
            about = next((ln.strip() for ln in self.get_context(pid).splitlines() if ln.strip() and not ln.startswith("#")), "")
            jobs = [j.get("name", "?") for j in self.agent_jobs(pid)]
            last = self._last_message_ts(pid)
            busy = sum(1 for x in running if x["project"] == pid) + (1 if self.chat_busy(pid) else 0)
            waiting = sum(1 for x in approvals if x["project"] == pid)
            said = self.messages(pid, 1)
            said = plain_text(said[0]["text"] or "").replace("\n", " ")[:160] if said and said[0]["role"] == "ken" else ""
            lines.append(
                f"- {a['name']} ({pid}): {about[:200] or 'no context yet'}"
                + (f" · last active {time.strftime('%a %d %b %H:%M', time.localtime(last))}" if last else " · not used yet")
                + (f" · {busy} working now" if busy else "")
                + (f" · jobs: {', '.join(jobs)}" if jobs else "")
                + (f" · {waiting} waiting for their OK" if waiting else "")
                + (f' · last said: "{said}"' if said else "")
            )
        if not self.list_agents():
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
        """Its tools.json (Ken's is ~/.ken/tools.json). Without one it gets every built-in tool, and Ken
        itself always does: connecting a tool in Settings is the only switch it has."""
        try:
            data = json.loads(self._tools_file(pid).read_text())
        except (OSError, ValueError):
            data = {}
        use = data.get("use", list(BUILTIN_TOOLS)) if isinstance(data, dict) and pid != HOME else list(BUILTIN_TOOLS)
        mcp = data.get("mcp", {}) if isinstance(data, dict) else {}
        return {"use": [t for t in use if t in BUILTIN_TOOLS], "mcp": mcp if isinstance(mcp, dict) else {}}

    def agent_tools(self, pid: str) -> dict:
        cfg = self.tools_config(pid)
        return {**self.tools(set(cfg["use"]), self.backend(pid)), **cfg["mcp"]}

    def sync_skills(self) -> None:
        try:
            gather_skills(self.home, self.other_skills)
        except OSError as e:
            log.warning("couldn't gather skills: %s", e)

    def _share_skills(self, cwd: Path) -> None:
        """Codex finds skills in <folder>/.agents/skills: Ken's own folders point it at the skills in use.
        A folder of theirs (an agent's code or info folder) is never written to."""
        use, cwd = self.home / ".skills-in-use", Path(cwd)
        link = cwd / ".agents" / "skills"
        if not use.is_dir() or self.home.resolve() not in cwd.resolve().parents:
            return
        try:
            if link.is_symlink() and os.path.realpath(link) == os.path.realpath(self.home / "skills"):
                _unlink(link)  # older Ken pointed it at <home>/skills
            if os.path.lexists(link):
                return
            link.parent.mkdir(exist_ok=True)
            link_dir(link, use)
        except OSError as e:
            log.warning("couldn't share Ken's skills with Codex in %s: %s", cwd, e)

    def _tools_stamp(self, pid: str) -> tuple:
        """Changes when its tools, keys or skills do, so an open chat reopens with them."""
        # The apps server too (hosted Ken: bot.use_apps_server moves it without a restart).
        out = [self.backend(pid), os.environ.get("KEN_APPS_MCP_URL", "")]
        others = [d for dirs in self.other_skills.values() for d in dirs]
        for f in (self._tools_file(pid), self.keys_dir(pid) / "keys.json", self.keys_dir(pid) / "files", self.home / "skills",
                  self.home / "skills-off.json", *others):
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

    def home_name(self) -> str:
        """What they call Ken: the '# You are <Name>' title of its soul file (Ken until they name it)."""
        try:
            title = next((ln for ln in self.context_file(HOME).read_text(errors="replace").splitlines() if ln.strip()), "")
        except OSError:
            return "Ken"
        m = re.match(r"#\s*You are\s+([^(\n]+)", title)
        return (m.group(1).strip().rstrip(",.")[:40] if m else "") or "Ken"

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
        out = {"id": pid, "name": self.home_name() if pid == HOME else self.get_agent(pid)["name"], "context": context,
               "memory": notes, "files": self.list_files(pid)}
        out.update(tools=self.tools_config(pid), builtin=list(BUILTIN_TOOLS), keys=self.list_keys(pid),
                   path="" if pid == HOME else self.get_agent(pid).get("path", ""),
                   skills=self.list_skills() if pid == HOME else [])
        return out

    def list_skills(self) -> list[dict]:
        """Ken's skills (one folder each in <home>/skills), then the ones from the person's other AI
        apps, with which app and whether it's on. Each is named and described by its SKILL.md."""
        out = [{"id": f.name, **self._skill_meta(f)} for f in _skill_folders(self.home / "skills")]
        mine, off = {s["id"] for s in out}, skills_off(self.home)
        for sid, app, f in other_skills(self.other_skills):
            out.append({"id": sid, **self._skill_meta(f), "app": app, "on": sid not in off,
                        **({"same_name": True} if f.name in mine else {})})
        return out

    @staticmethod
    def _skill_meta(f: Path) -> dict:
        meta, text = {}, (f / "SKILL.md").read_text(errors="replace")
        if text.startswith("---"):
            for line in text.split("---", 2)[1].splitlines():
                key, _, value = line.partition(":")
                if key.strip() in ("name", "description"):
                    meta[key.strip()] = value.strip().strip("\"'")
        return {"name": meta.get("name") or f.name, "about": meta.get("description", "")[:200]}

    def _skill_dir(self, sid: str) -> Path:
        if ":" in sid:
            found = next((f for i, _, f in other_skills(self.other_skills) if i == sid), None)
            if not found:
                raise KeyError(sid)
            return found
        d = self.home / "skills" / sid
        if not sid or "/" in sid or "\\" in sid or sid.startswith(".") or not d.is_dir():
            raise KeyError(sid)
        return d

    def switch_skill(self, sid: str, on: bool) -> list[dict]:
        """A skill from another app, on or off for Ken. Its own folder is left as it is."""
        if ":" not in sid:
            raise KeyError(sid)
        self._skill_dir(sid)
        off = skills_off(self.home)
        off = off - {sid} if on else off | {sid}
        (self.home / "skills-off.json").write_text(json.dumps(sorted(off), indent=2) + "\n")
        self.sync_skills()
        return self.list_skills()

    def read_skill(self, sid: str) -> dict:
        """A skill to read: its SKILL.md (without the front matter) and what else its folder holds."""
        d = self._skill_dir(sid)
        text = (d / "SKILL.md").read_text(errors="replace") if (d / "SKILL.md").is_file() else ""
        if text.startswith("---"):
            text = text.split("---", 2)[2] if text.count("---") >= 2 else text
        meta = next((s for s in self.list_skills() if s["id"] == sid), {"name": sid, "about": ""})
        parts = sorted(f.name for f in d.iterdir() if not f.name.startswith(".") and f.name not in ("SKILL.md", "LICENSE", "LICENSE.txt", "assets", "agents"))
        return {**meta, "text": text.strip(), "parts": parts}

    def remove_skill(self, sid: str) -> list[dict]:
        if ":" in sid:
            raise KeyError(sid)  # another app's skill: switch it off instead
        d = self._skill_dir(sid)
        if (d / ".from-ken").exists():
            (d.parent / f".removed-{sid}").touch()
        d.unlink() if d.is_symlink() else shutil.rmtree(d)
        return self.list_skills()

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

    def _session_env(self, pid: str) -> dict:
        """What a chat's AI session runs with: its keys, and which chat it is (so `ken --library show` lands there)."""
        return {**self._key_values(pid), "KEN_CHAT": pid}

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
            parts.append(WHERE[via])  # bot.py says more for a Ken in the cloud (where_notes_for_hosted)
        jobs = self.list_runs(project=pid, active=True) if pid == HOME else []
        if pid == HOME:  # stopped by them or by a restart: Ken hears it once, so it never says one is still going
            seen = float(self.kv_get("jobs:seen") or 0)
            stopped = [r["title"] for r in self.list_runs(project=HOME, active=False, since=time.time() - 86400)
                       if r["status"] == "stopped" and (r["finished"] or 0) > seen]
            self.kv_set("jobs:seen", str(time.time()))
            if stopped:
                parts.append("background jobs stopped, not finished: " + ", ".join(f'"{t}"' for t in stopped))
        said = json.loads(self.kv_get(f"jobs:said:{pid}") or "[]")
        if said:
            self.kv_set(f"jobs:said:{pid}", "[]")
            parts.append("since your last turn your scheduled jobs sent them: " + " | ".join(said))
        told = self.kv_get(f"onboarding:said:{pid}")
        if told:
            self.kv_set(f"onboarding:said:{pid}", "")
            parts.append("while your computer was being set up they told the site this. It isn't saved anywhere yet: update your SOUL.md "
                         "with it now, in your own words (who they are, what they want from you, how they like to be talked to), "
                         "then use it and don't ask again: " + told)
        call = self.kv_get(f"voice:said:{pid}")
        if call:
            self.kv_set(f"voice:said:{pid}", "")
            parts.append("since your last turn they talked with your voice side on a call: " + call)
        if jobs:
            parts.append("background jobs running: " + ", ".join(
                f'{r["id"]} "{r["title"]}" ({r["status"]}, {int((time.time() - r["created"]) // 60)}m{self._latest_steps(r["id"])})' for r in jobs))
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

    async def _dead_claude(self, msg: str, action: str, via: str | None = None) -> tuple[str, str]:
        """Their own Claude refused: if its sign-in is dead, it's forgotten (Settings shows "Not connected" with a plain
        Connect). Ken never moves to Ken AI credits by itself: it says how, when the plan has them."""
        from onboarding import included_ai_key, own_claude

        try:
            forgotten = bool(self.forget_dead_claude and await self.forget_dead_claude())
        except Exception:
            log.exception("checking the Claude sign-in failed")
            return msg, action
        if not forgotten and own_claude():  # still signed in (a blip): as it was
            return msg, action
        if not included_ai_key():
            return "Your Claude sign-in stopped working. Connect it again to carry on.", "reconnect"
        return ("Your Claude sign-in stopped working. Connect it again in " + settings_ai(via)
                + ", or switch to Ken AI credits there to keep going now."), "reconnect"

    def backend(self, pid: str) -> str:
        return self.kv_get(f"backend:{pid}") or self.brain.name

    def brain_for(self, pid: str) -> Brain:
        name = self.backend(pid)
        return self.brain if name == self.brain.name else self.brains[name]

    def model_choice(self, pid: str) -> str:
        if pid != HOME:
            return self.get_agent(pid).get("model", "")
        try:
            return (self.home / ("model-codex" if self.backend(pid) == "codex" else "model")).read_text().strip()
        except OSError:
            return ""

    def credits_choice(self) -> str:
        """The model picked for Ken AI credits (Haiku or Sonnet), kept apart from the model picked for their own
        Claude or ChatGPT, so switching between the two never mixes them up. "" until they pick."""
        try:
            chosen = (self.home / "model-credits").read_text().strip()
        except OSError:
            return ""
        return chosen if chosen in CREDIT_MODELS else ""

    def model_for(self, pid: str) -> str:
        # On Ken AI credits only the credits models run: Haiku by default, Sonnet if they picked it (paid plans).
        if self.credits_model and (credits := self.credits_model(self.backend(pid))):
            return self.credits_choice() or credits
        return self.model_choice(pid) or getattr(self.brain_for(pid), "model_fn", lambda: "")()

    async def runtime(self, pid: str) -> dict:
        """The chat's brain and model, and what the model menu may offer: only the AI Ken can run on now (never models
        from an account that isn't connected), and on Ken AI credits only their model ("credits": true)."""
        backend = self.backend(pid)
        credits = self.credits_model(backend) if self.credits_model else ""
        current = (self.credits_choice() or credits) if credits else self.model_choice(pid)
        choices = list(CREDIT_MODELS) if credits else await self.model_choices(backend) if self.model_choices else []
        usable = self.usable_brains() if self.usable_brains else list(self.brains)
        return {"backend": backend, "current": current, "choices": choices,
                "providers": [b for b in self.brains if b in usable] or [backend], "credits": bool(credits)}

    async def set_runtime(self, pid: str, *, backend=None, model=None) -> None:
        if pid != HOME:
            self._adir(pid)
        backend = self.backend(pid) if backend is None else backend
        if not isinstance(backend, str) or backend not in self.brains:
            raise ValueError("Choose Claude or Codex.")
        if model is not None and (not isinstance(model, str) or len(model) > 500):
            raise ValueError("Invalid model.")
        on_credits = bool(self.credits_model and self.credits_model(backend))
        if on_credits and model is not None:
            if model and model not in CREDIT_MODELS:
                raise ValueError("On Ken AI credits Ken picks the model. Connect your own Claude or ChatGPT in Settings for other models.")
            (self.home / "model-credits").write_text(model.strip() + "\n")  # their own account's pick is left as it was
            self.broadcast("chat.runtime", pid, backend=backend)
            return
        if model and self.model_choices and model not in await self.model_choices(backend):
            raise ValueError("Choose a model available for this account.")
        if self.chat_busy(pid) or self.list_runs(project=pid, active=True):
            raise ValueError("Wait for this chat to finish or stop it before changing its brain.")
        async with self.chat_locks.setdefault(pid, asyncio.Lock()):
            if backend != self.backend(pid):
                session = self.chats.pop(pid, None)
                self.kv_set(f"backend:{pid}", backend)
                if session is not None:
                    await session.close()
            if model is not None:
                if pid == HOME:
                    (self.home / ("model-codex" if backend == "codex" else "model")).write_text(model.strip() + "\n")
                else:
                    self.update_agent(pid, model=model)
            self.broadcast("chat.runtime", pid, backend=backend)

    async def use_brain(self, pid: str, backend: str) -> None:
        """set_runtime's brain switch, but also from inside a reply (Ken asked to switch AI): that reply finishes on the
        AI it started on, and the next one opens on this one (it hears the recent conversation)."""
        if backend == self.backend(pid):
            return
        if not (self.chat_busy(pid) or self.list_runs(project=pid, active=True)):
            return await self.set_runtime(pid, backend=backend)
        if backend not in self.brains:
            raise ValueError("Choose Claude or Codex.")
        self.kv_set(f"backend:{pid}", backend)
        self.chat_tools.pop(pid, None)  # reopens once it's free
        self.broadcast("chat.runtime", pid, backend=backend)

    def session_key(self, pid: str, backend=None) -> str:
        # Keep legacy Claude IDs usable; other runtimes own separate resume tokens.
        return f"session:codex:{pid}" if (backend or self.backend(pid)) == "codex" else f"session:{pid}"

    def _chat_session(self, pid: str):
        s = self.chats.get(pid)
        if s is not None and self.chat_tools.get(pid) != self._tools_stamp(pid) and not s.busy:
            self.chats.pop(pid)  # its tools or keys changed: reopen with the new ones
            self._bg.append(asyncio.create_task(s.close()))
            s = None
        if s is None:
            brain = self.brain_for(pid)
            self.sync_skills()
            if brain.name == "codex":
                self._share_skills(self.home_cwd if pid == HOME else self.workdir(self.get_agent(pid)))
            self.chat_tools[pid] = self._tools_stamp(pid)
            if pid == HOME:
                s = brain.session(cwd=str(self.home_cwd), system=lambda: self.system(pid), resume=self.kv_get(self.session_key(pid)),
                                  model_fn=lambda: self.model_for(pid),
                                  mcp_fn=lambda: self.agent_tools(pid), env_fn=lambda: self._session_env(pid))
            else:
                s = brain.session(
                    cwd=str(self.workdir(self.get_agent(pid))), system=lambda: self.system(pid),
                    resume=self.kv_get(self.session_key(pid)),
                    model_fn=lambda: self.model_for(pid),
                    mcp_fn=lambda: self.agent_tools(pid),
                    env_fn=lambda: self._session_env(pid),
                )
            if s.session_id and getattr(s, "spent", {}) is None and (saved := self.kv_get(f"spent:{s.session_id}")):
                s.spent = json.loads(saved)  # what it had used before, so its next message counts only itself
            self.chats[pid] = s
        return s

    def record_usage(self, session, pid: str, kind: str, **extra) -> None:
        """What one message or job used, per model: a line in usage.jsonl here, and the same numbers
        in usage stats (off with KEN_USAGE_STATS=0). Never what was said. Fails silently: counting
        must never get in the way of an answer."""
        try:
            self._record_usage(session, pid, kind, **extra)
        except Exception as e:
            log.debug("usage not recorded: %s", e)

    def _record_usage(self, session, pid: str, kind: str, **extra) -> None:
        rows = getattr(session, "usage", None) or []
        if getattr(session, "spent", None) is not None and session.session_id:
            self.kv_set(f"spent:{session.session_id}", json.dumps(session.spent))
        if not rows:
            return
        brain = self.brain_for(pid).name
        try:
            with open(self.home / "usage.jsonl", "a") as f:
                for r in rows:
                    f.write(json.dumps({"ts": round(time.time()), "kind": kind, "brain": brain, **extra, **r}) + "\n")
        except OSError:
            pass
        # On Ken AI credits (the host pays): marked, so the cost of the credits per person can be summed.
        paid_by_host = {"credits": True} if self.credits_model and self.credits_model(self.backend(pid)) else {}
        for r in rows:
            crash.track("ai_usage", kind=kind, brain=brain, agent=pid != HOME, **extra, **paid_by_host, **r)

    def chat_busy(self, pid: str) -> bool:
        lock = self.chat_locks.get(pid)
        return bool(lock and lock.locked())

    async def _reply_aside(self, pid: str, text: str, body: str, via: str | None, listener) -> bool:
        """Answer a new message in a second session while the chat is busy; the work in progress carries on and reports
        back as usual. False when the message is about that work (the chat then takes it) or no answer came."""
        context = "\n".join(f"{m['role']}: {m.get('text', '')[:2000]}" for m in self.messages(pid, limit=12))
        note = ("\n\n=== You're busy in the main chat ===\nYou are still working on an earlier request there; that work "
                "carries on and reports back by itself. They've just written something new: answer it here, now, fully. "
                "If it is about the work in progress (a change to it, a correction, stop, how it's going), reply with only "
                "the word STEER and the main chat takes it. Don't redo or repeat the work in progress.")
        cwd = self.home_cwd if pid == HOME else self.workdir(self.get_agent(pid))
        if self.brain_for(pid).name == "codex":
            self._share_skills(cwd)
        session = self.brain_for(pid).session(
            cwd=str(cwd), system=lambda: self.system(pid) + note, model_fn=lambda: self.model_for(pid),
            mcp_fn=lambda: self.agent_tools(pid), env_fn=lambda: self._session_env(pid))
        said: list[str] = []

        async def on_text(t: str) -> None:
            t = self.mask(pid, t)
            if t.strip() in ("STEER", "NOTHING_TO_SAY"):
                return
            said.append(t)
            self.log_message(pid, "ken", t, **({"via": via} if via else {}))
            if listener is not None:
                await listener(t)

        try:
            await until_idle(session, session.ask(
                f"Recent conversation (context):\n{context}\n\n{time.strftime('%a %d %b %H:%M')}, "
                f"{WHERE.get(via, 'from them')}. Their new message:\n{body}", on_text))
        except Exception as e:
            log.warning("side reply for %s failed: %s", pid, e)
        finally:
            self.record_usage(session, pid, "chat", via=via or "app")
            await session.close()
        if said:
            self._aside.setdefault(pid, []).append(f"they: {text[:2000]}\nyou: {' '.join(said)[:2000]}")
        return bool(said)

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
        origin: int | None = None,
        steer: bool = False,
        library: list[dict] | None = None,
    ) -> bool:
        """Talk to Ken (or Ken-on-a-project). Replies stream to every screen;
        `listener` also gets each finished utterance (Telegram uses it).
        `steer`: something they wrote. If Ken is mid-reply it joins that reply, the way Claude Code
        takes messages while it works, instead of waiting for it to finish."""
        if pid != HOME:
            self.get_agent(pid)
        if self.is_off():
            return False
        files = [f for f in (files or []) if f]
        if log_user:
            self.log_message(pid, "you", text, files=files, **({"via": via} if via else {}))
        body = prompt if prompt is not None else text
        library = clean_library(library)
        if library and prompt is None:
            body += "\n\n(" + library_note(library) + ")"
        if files and prompt is None:
            files = [await self._readable_picture(pid, f) for f in files]
            said = [f for f in files if re.match(r"voice-.*\.(m4a|webm|ogg|oga|mp3|wav)$", f, re.I)] if text.strip() else []
            others = [f for f in files if f not in said]
            if others:
                body += f"\n\n(Attached in {self.files_dir(pid)}: {', '.join(others)})"
            if said:  # a voice note: the words above are already what they said, no need to open the recording
                body += f"\n\n(They said this in a voice note; it's already written out above. The recording is in {self.files_dir(pid)}: {', '.join(said)}, only if they ask about how it sounds.)"
        lock = self.chat_locks.setdefault(pid, asyncio.Lock())
        session = self.chats.get(pid)
        if steer and lock.locked() and session is not None and session.busy:
            # Ken is busy with something: a new message is answered at once on the side, so the chat never makes them
            # wait. One about the work in progress joins that work instead, the way Claude Code takes messages.
            if text.strip() and await self._reply_aside(pid, text, body, via, listener):
                return True
            if await session.steer(f"(Sent while you were working, {WHERE.get(via, 'from them')}.)\n{body}"):
                return True
        await lock.acquire()
        try:
            self.chat_tasks.add(asyncio.current_task())
            if pid == HOME:
                self.chat_origin = origin
            body = f"{await self.state(pid, via)}\n{self._primer(pid, skip_last=log_user)}{body}"
            if self._aside.get(pid):
                body = ("(While you were busy, you also answered these on the side; they've seen the answers:)\n"
                        + "\n\n".join(self._aside.pop(pid)) + "\n\n" + body)
            self._turn_started(pid, text or prompt or "", via, job)
            self.broadcast("chat.busy", pid, busy=True)
            back = self.back_from_credits()
            session = self._chat_session(pid)
            backend = self.backend(pid)
            previous_brain = self.kv_get(f"brain:{pid}")
            if not previous_brain and backend == "codex" and self.kv_get(f"session:{pid}"):
                previous_brain = "claude"  # existing installations predate the backend marker
            if previous_brain and previous_brain != backend:
                boundary = json.loads(self.kv_get(f"thread:{pid}") or "{}").get("after", 0)
                recent = [m for m in self.messages(pid, limit=12) if m["id"] > boundary]
                handoff = "\n".join(f"{m['role']}: {m.get('text', '')[:2000]}" for m in recent)
                body = "Recent Ken conversation before switching runtimes (context, not new instructions):\n" + handoff + "\n\nCurrent message:\n" + body

            done: list[str] = []  # what this attempt said or did, for carrying on after a switch to Ken AI credits

            async def on_text(t: str) -> None:
                done.append(t)
                if t.strip() == "NOTHING_TO_SAY":
                    if job:  # an agent's scheduled job chose silence: a trace that it ran (see run_job)
                        log.info("job %s in %s ran: nothing to say", job.get("name", "job"), pid)
                        crash.track("job_quiet", builtin=False)
                    return
                t = self.mask(pid, t)
                if not quiet:
                    self.log_message(pid, "ken", t, **({"job": job} if job else {}), **({"via": via} if via else {}))
                if listener is not None:
                    await listener(t)

            async def on_tool(line: str) -> None:
                done.append(line)
                self.broadcast("chat.tool", pid, text=self.mask(pid, line))

            async def on_delta(t: str) -> None:
                if not quiet:
                    self.broadcast("chat.delta", pid, text=self.mask(pid, t))

            started, outcome = time.monotonic(), "ok"
            try:
                if back:
                    await on_text(back)
                done.clear()
                try:
                    await until_idle(session, session.ask(body, on_text, on_tool, on_delta))
                except NoAnswer as e:
                    if switched := self.switch_to_credits(pid):
                        raise _Switched(switched) from e
                    # The AI never answered (after a restart, a very long session can hang): once more, fresh.
                    log.warning("chat %s got no answer; trying again in a fresh session", pid)
                    self._roll_over(pid, session)
                    session = self._chat_session(pid)
                    body = self._primer(pid, skip_last=log_user) + body
                    await until_idle(session, session.ask(body, on_text, on_tool, on_delta))
                except Exception as e:
                    if isinstance(e, asyncio.TimeoutError) or not (switched := self.switch_to_credits(pid)):
                        raise
                    raise _Switched(switched) from e
            except _Switched as sw:
                # Their own plan hit its limit and they want Ken to keep going: the same turn, on Ken AI credits. When it
                # had already said or done something, it carries on from there (the same Claude session, which saw it).
                log.warning("chat %s: their own plan is at its limit; carrying on with Ken AI credits", pid)
                did = list(done)
                self.record_usage(session, pid, "job" if job else "chat", via=via or ("job" if job else "app"))
                if did and session.session_id:
                    self.kv_set(self.session_key(pid, backend), session.session_id)
                with contextlib.suppress(Exception):
                    await session.close()
                self.chats.pop(pid, None)
                await on_text(str(sw))
                session = self._chat_session(pid)
                name = "ChatGPT" if backend == "codex" else "Claude"
                if self.backend(pid) != backend:  # a ChatGPT chat now on Claude: it hears the recent conversation
                    backend = self.backend(pid)
                    boundary = json.loads(self.kv_get(f"thread:{pid}") or "{}").get("after", 0)
                    recent = [m for m in self.messages(pid, limit=12) if m["id"] > boundary]
                    body = ("Recent Ken conversation before switching runtimes (context, not new instructions):\n"
                            + "\n".join(f"{m['role']}: {m.get('text', '')[:2000]}" for m in recent) + "\n\nCurrent message:\n"
                            + body + ("\n\n" + carry_on(name, did) if did else ""))
                elif did:
                    body = carry_on(name)
                try:
                    await until_idle(session, session.ask(body, on_text, on_tool, on_delta))
                except asyncio.CancelledError:
                    outcome = "stopped"
                    raise
                except Exception as e:
                    outcome = await self._chat_failed(e, pid, session, text, job, log_user, via, listener)
            except asyncio.CancelledError:
                outcome = "stopped"
                raise
            except Exception as e:
                outcome = await self._chat_failed(e, pid, session, text, job, log_user, via, listener)
            finally:
                crash.track("message", via=via or ("job" if job else "app"), agent=pid != HOME, files=len(files),
                            spoke_first=not log_user and not job, outcome=outcome, seconds=round(time.monotonic() - started, 1))
                self.record_usage(session, pid, "job" if job else "chat", via=via or ("job" if job else "app"))
                if session.session_id:
                    self.kv_set(self.session_key(pid, backend), session.session_id)
                    self.kv_set(f"brain:{pid}", backend)
                if outcome == "ok" and session.context_tokens > min(ROLL_OVER_TOKENS, (session.context_window or DEFAULT_WINDOW) / 2):
                    self._roll_over(pid, session)  # heavy: slower and dearer every message, and slow to pick up again
                if outcome != "stopped" or not self.stopping:
                    self._turn_ended(pid)  # cut off by Ken shutting down: kept, so the next start carries on
                self.broadcast("chat.busy", pid, busy=False)
            return outcome == 'ok'
        finally:
            if pid == HOME:
                self.chat_origin = None
            lock.release()
            self.chat_tasks.discard(asyncio.current_task())

    async def _chat_failed(self, e, pid, session, text, job, log_user, via, listener) -> str:
        """A reply that failed: one plain line, with the one thing to do about it. Returns the outcome."""
        outcome = "timeout" if isinstance(e, asyncio.TimeoutError) else "error"
        if outcome == "error":
            crash.report(e, f"chat failed: {e}", where="engine.send_message")
        log.warning("chat %s failed: %s", pid, e)
        await session.close()
        self.chats.pop(pid, None)
        quiet = isinstance(e, asyncio.TimeoutError) and not isinstance(e, NoAnswer)
        msg, action = (("Ken went quiet for 45 minutes, so I stopped. Tell me to carry on and I'll pick it up.", "retry")
                       if quiet else self.explain(e, pid, via))
        if action == "reconnect" and msg.endswith("connection stopped working.") and self.backend(pid) == "claude":
            msg, action = await self._dead_claude(msg, action, via)
        # The one thing to do about it shows as a button beside it in the app (a job's failure has nothing to retry).
        # Ken speaking first (its hello) that failed: there's no message of theirs to retry, so a plain "try again"
        # says nothing. But when the way forward is theirs to take (no Ken AI credits left, an account to connect),
        # say it once with its button; that message also stops the hello being tried again and again.
        if not text.strip() and not job and not log_user and action == "retry":
            return outcome
        if job:  # an agent's scheduled job: say which one didn't run, and what happens next
            msg = self.job_missed(job, msg)
        self.log_message(pid, "ken", msg, error=True, **({} if job and action == "retry" else {"action": action}),
                         **({"job": job} if job else {}),
                         **({"brain": "codex" if self.backend(pid) == "codex" else "claude"}), **({"via": via} if via else {}))
        if listener is not None:
            await listener(msg)
        return outcome

    # ---- a reply cut off by a restart (the computer restarted, Ken updated, or crashed) ----
    # While a chat turn runs, a small note on disk says so; it's cleared when the turn ends. A note still there when Ken
    # starts means the turn was cut off: Ken says so in one line and carries on in the same conversation.
    @property
    def _turns_file(self) -> Path:
        return self.home / "turns-in-progress.json"

    def _turns(self) -> dict:
        try:
            data = json.loads(self._turns_file.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_turns(self, turns: dict) -> None:
        try:
            if turns:
                self._turns_file.write_text(json.dumps(turns))
            else:
                self._turns_file.unlink(missing_ok=True)
        except OSError:
            log.warning("couldn't keep the turn-in-progress note", exc_info=True)

    def _turn_started(self, pid: str, asked: str, via: str | None, job: dict | None) -> None:
        turns = self._turns()
        turns[pid] = {"since": time.time(), "asked": asked.strip()[:1500], "via": via or "", "job": bool(job)}
        self._write_turns(turns)

    def _turn_ended(self, pid: str) -> None:
        turns = self._turns()
        if turns.pop(pid, None) is not None:
            self._write_turns(turns)

    async def resume_interrupted(self) -> list[str]:
        """On start: carry on with every chat whose last turn was cut off. Returns those chats."""
        turns = self._turns()
        # Only what an earlier run left behind; a turn this run already started stays recorded until it ends.
        self._write_turns({pid: t for pid, t in turns.items() if float(t.get("since", 0)) >= self._booted})
        resumed = []
        for pid, turn in turns.items():
            if pid != HOME and pid not in {a["id"] for a in self.list_agents()}:
                continue
            if time.time() - float(turn.get("since", 0)) > 6 * 3600:
                continue  # long ago: picking it up now would surprise them more than help
            if float(turn.get("since", 0)) >= self._booted:
                continue  # started since this run began (a first hello while Ken was still starting up): not cut off
            via = turn.get("via") or None
            asked = turn.get("asked") or "(their last message in this chat)"
            prompt = (
                "(Your last reply was cut off: the computer you run on restarted in the middle of it. First say exactly "
                "this one short line: \"Sorry, I was interrupted. Picking up where I left off.\" Then finish what they "
                f"asked, in this same conversation: \"{asked}\". Check what's already done before redoing anything. If "
                "what's left would send, post, publish, pay, delete or change something for them (an email, a post, a "
                "message to someone, a purchase, a file they care about) and you can't be sure it didn't already happen "
                "before the restart, ask them before doing it again instead of repeating it.)")
            listener = self.resume_listener(via) if self.resume_listener else None
            self._bg.append(asyncio.create_task(self.send_message(pid, "", log_user=False, via=via, prompt=prompt, listener=listener)))
            resumed.append(pid)
            log.info("resuming %s after a cut-off reply", pid)
        return resumed

    async def run_job(self, pid: str, prompt: str, *, job: dict | None = None,
                      listener: Callable[[str], Awaitable[None]] | None = None, preface: str = "", silent: bool = False) -> str:
        """A scheduled job in a fresh session of its own, so nothing left over in the chat can steer
        it, and only its final answer reaches them, after `preface` (a caught-up job says it's late).
        `silent`: work done only for Ken itself (learning about them); nothing reaches the chat, not even a failure."""
        if self.is_off():
            return ""
        cwd = self.home_cwd if pid == HOME else self.workdir(self.get_agent(pid))
        new = lambda resume=None: self.brain_for(pid).session(
            cwd=str(cwd), system=lambda: self.system(pid), resume=resume, model_fn=lambda: self.model_for(pid),
            mcp_fn=lambda: self.agent_tools(pid), env_fn=lambda: self._session_env(pid))
        if not silent and (back := self.back_from_credits()):
            self.log_message(pid, "ken", back)
        session = new()
        last, done = "", []

        async def on_text(t: str) -> None:
            nonlocal last
            last = t
            done.append(t)

        async def on_tool(line: str) -> None:
            done.append(line)

        failed, switched = "", ""
        ask = f"[{time.strftime('%a %d %b %H:%M')}]\n{prompt}"
        try:
            backend = self.backend(pid)
            try:
                final = await until_idle(session, session.ask(ask, on_text, on_tool))
            except Exception as e:
                if (isinstance(e, asyncio.TimeoutError) and not isinstance(e, NoAnswer)) or not (switched := self.switch_to_credits(pid)):
                    raise
                session = await self._on_credits(session, pid, "job", backend, new, done)
                final = await until_idle(session, session.ask(self._ask_on_credits(ask, backend, pid, done), on_text, on_tool))
            text = (final or last or "").strip()
        except Exception as e:
            log.warning("job %s failed: %s", (job or {}).get("name", "job"), e)
            text, failed = "", self.explain(e, pid)[0]
        finally:
            name = (job or {}).get("name")  # a job they named themselves stays "custom": names are theirs
            self.record_usage(session, pid, "job", job=name if name in ("morning-brief", "nightly-review") else "custom")
            try:
                await session.close()
            except Exception:
                pass
        if silent:
            log.info("silent job %s: %s", (job or {}).get("name", "job"), "failed: " + failed if failed else "done")
            return ""
        if text == "NOTHING_TO_SAY":
            # Silent by design, but leave a trace, so it can be seen later that the job ran and chose to say nothing.
            log.info("job %s ran: nothing to say", (job or {}).get("name", "job"))
            crash.track("job_quiet", builtin=name in ("morning-brief", "nightly-review"))
            return ""
        if failed or not text:
            # A job that didn't run must never just vanish: they'd wait for a brief that never comes. One plain line.
            # (One that ran but said nothing may still have done things, so it isn't offered to run again as if not.)
            text, preface = self.job_missed(job, failed) if failed else (
                f"⏰ Your {self._job_name(job)} ran but came back with nothing this time. Ask me if you want it again."), ""
        text = self.mask(pid, text)
        if not text:
            return ""
        if preface:
            text = f"{preface}\n\n{text}"
        if switched:
            text = f"{switched}\n\n{text}"
        self.log_message(pid, "ken", text, **({"job": job} if job else {}))
        # The chat's own session didn't see this; it hears it with their next message.
        said = json.loads(self.kv_get(f"jobs:said:{pid}") or "[]")[-4:]
        self.kv_set(f"jobs:said:{pid}", json.dumps(said + [f'{(job or {}).get("label", "Scheduled job")}: {text[:1500]}']))
        if listener is not None:
            await listener(text)
        return text

    @staticmethod
    def _job_name(job: dict | None) -> str:
        """How a job reads in the middle of a sentence: the label "⏰ Brand deals" is "brand deals"."""
        what = re.sub(r"^\W+", "", (job or {}).get("label") or "") or "scheduled job"
        return what[0].lower() + what[1:]

    @classmethod
    def job_missed(cls, job: dict | None, why: str) -> str:
        """The plain line for a scheduled job that didn't run, and what happens next."""
        return f"⏰ Your {cls._job_name(job)} didn't run just now. {why} " + (
            "Ask me to run it now." if (job or {}).get("once") else "It'll try again next time, or ask me to run it now.")

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
            "into a clear brief of your job, goal and rules. If the job has a rhythm, propose the schedule in your message "
            "and put it in jobs.json only once they say yes. Write "
            "tools.json with only the built-in tools you need, plus any MCP server that would help. "
            f'In {self._adir(pid) / "agent.json"}, put one emoji that fits your job as "emoji" (your picture in their chat list), and if '
            'you\'re still called "New agent", give yourself a short name (one to three words) as "name". Then tell them in '
            "a few short lines what you'll do and when, and ask for the first thing you need from them (a login, a "
            "key, a link), one thing at a time. Keys go on this agent's Brain page, under Accounts & passwords, never in this chat.)"
        ))

    async def files_dropped(self, pid: str, names: list[str]) -> None:
        self.log_message(pid, "you", "", files=names, via="app")
        await self.send_message(
            pid, "", log_user=False, via="app", prompt=f"(They dropped in {', '.join(names)}, saved in {self.files_dir(pid)}.)"
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

    async def reset_chat(self, pid: str, *, clear: bool = False) -> bool:
        """A fresh thread. Clearing also files what's worth keeping from the old one into memory, in the background;
        True when that save started."""
        lock = self.chat_locks.setdefault(pid, asyncio.Lock())
        if lock.locked():
            raise ValueError("Ken is still working. Stop or finish the reply before clearing this thread.")
        saving = False
        async with lock:
            # Archive by boundary, rather than deleting messages, transcripts or memory.
            if clear:
                previous = json.loads(self.kv_get(f"thread:{pid}") or "{}")
                after = previous.get("after", 0)
                thread = [m for m in self.messages(pid) if m["id"] > after]
                if any(m["role"] == "you" and str(m.get("text", "")).strip() for m in thread):
                    self._bg.append(asyncio.create_task(self._save_thread(pid, thread)))
                    saving = True
                boundary = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE project=?", (pid,)).fetchone()[0]
                # Where the thread started before, so this Clear can be undone (unclear_chat).
                self.kv_set(f"thread:{pid}", json.dumps({"after": boundary, "cleared_at": time.time(),
                                                          "before": {"after": after, "cleared_at": previous.get("cleared_at", 0)}}))
            s = self.chats.pop(pid, None)
            self.db.execute("DELETE FROM kv WHERE k IN (?, ?)", (f"session:{pid}", f"session:codex:{pid}"))
            self.db.execute("DELETE FROM kv WHERE k=?", (f"brain:{pid}",))
            if s is not None:
                await s.close()
            self.broadcast("chat.reset", pid)
        return saving

    # A conversation that has grown heavy, or a session the AI stopped answering, starts over: what mattered goes into
    # memory (as Clear does), the AI gets a fresh session, and the next message carries the last few for context. The
    # chat on screen doesn't change.
    async def unclear_chat(self, pid: str) -> None:
        """Undo the latest Clear: the thread starts where it did before. The AI got a fresh session on Clear, so the
        next message carries the last few for context, as after a roll-over."""
        lock = self.chat_locks.setdefault(pid, asyncio.Lock())
        if lock.locked():
            raise ValueError("Ken is still working. Stop or finish the reply first.")
        async with lock:
            before = json.loads(self.kv_get(f"thread:{pid}") or "{}").get("before")
            if before is None:
                raise ValueError("There's nothing to undo.")
            self.kv_set(f"thread:{pid}", json.dumps(before))
            self.kv_set(f"primer:{pid}", "1")
            self.broadcast("chat.reset", pid)

    def _roll_over(self, pid: str, session=None) -> None:
        after = max(json.loads(self.kv_get(f"thread:{pid}") or "{}").get("after", 0), int(self.kv_get(f"rolled:{pid}") or 0))
        thread = [m for m in self.messages(pid) if m["id"] > after]
        if any(m["role"] == "you" and str(m.get("text", "")).strip() for m in thread):
            self._bg.append(asyncio.create_task(self._save_thread(pid, thread)))
        if thread:
            self.kv_set(f"rolled:{pid}", str(thread[-1]["id"]))
        self.db.execute("DELETE FROM kv WHERE k IN (?, ?)", (f"session:{pid}", f"session:codex:{pid}"))
        self.kv_set(f"primer:{pid}", "1")
        old = session or self.chats.get(pid)
        self.chats.pop(pid, None)
        if old is not None:
            self._bg.append(asyncio.create_task(old.close()))
        log.info("chat %s starts a fresh session", pid)

    def _primer(self, pid: str, skip_last: bool) -> str:
        """The last few messages, for a fresh session, once."""
        if not self.kv_get(f"primer:{pid}"):
            return ""
        self.db.execute("DELETE FROM kv WHERE k=?", (f"primer:{pid}",))
        boundary = json.loads(self.kv_get(f"thread:{pid}") or "{}").get("after", 0)
        recent = [m for m in self.messages(pid, limit=21) if m["id"] > boundary][: -1 if skip_last else None][-20:]
        if not recent:
            return ""
        return ("(This conversation continues in a fresh session; what mattered earlier is in your memory. The last few "
                "messages, for context:)\n" + "\n".join(f"{'they' if m['role'] == 'you' else 'you'}: {str(m.get('text', ''))[:2000]}" for m in recent) + "\n\n")

    # The off switch: everything Ken is doing stops at once, mid-action (a reply deleting emails stops now, not after
    # its next step), and nothing starts again (no reply, brief, job or background work) until they turn it back on.
    # Their plan, memory and files are untouched.
    def is_off(self) -> bool:
        return (self.home / "switched-off").exists()

    async def switch(self, on: bool) -> None:
        note = self.home / "switched-off"
        if on:
            note.unlink(missing_ok=True)
        else:
            note.write_text(time.strftime("%Y-%m-%d %H:%M") + "\n")
            kill_children()
            for t in [*self.run_tasks.values(), *self.chat_tasks]:
                if t is not asyncio.current_task():
                    t.cancel()
            sessions = [*self.chats.values(), *self.run_sessions.values()]
            self.chats.clear()
            for s in sessions:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(s.close(), 5)
            for run in self.list_runs(active=True):
                self._set_run(run["id"], status="stopped", finished=time.time(), summary="Stopped: Ken was turned off.")
                self.emit("run.finished", project=run["project"], run=run["id"], status="stopped", title=run["title"], summary="Stopped: Ken was turned off.")
            for pid in list(self.chat_locks):
                self.broadcast("chat.busy", pid, busy=False)
        self.broadcast("ken.power", on=on)

    async def _save_thread(self, pid: str, thread: list[dict]) -> None:
        """A cleared thread, read once more in a session of its own: what's worth keeping goes into memory, the way the
        nightly review files the day."""
        text = "\n".join(f"{'they' if m['role'] == 'you' else 'you'}: {str(m.get('text', ''))[:3000]}" for m in thread)[-60000:]
        cwd = self.home_cwd if pid == HOME else self.workdir(self.get_agent(pid))
        session = self.brain_for(pid).session(
            cwd=str(cwd), system=lambda: self.system(pid), model_fn=lambda: self.model_for(pid),
            mcp_fn=lambda: self.agent_tools(pid), env_fn=lambda: self._session_env(pid))
        async def ignore(_: str) -> None:
            pass

        try:
            await until_idle(session, session.ask(
                f"[{time.strftime('%a %d %b %H:%M')}] They just cleared this chat. Here is the thread they put away:\n\n"
                f"{text}\n\nFile anything worth keeping from it into {self.memory_dir(pid)}: extend the topic file it "
                "belongs to, or start one if nothing fits, with facts dated inline. Corrections and standing rules go where "
                "you keep those. Skip small talk and anything already in memory. If nothing here is worth keeping, write "
                "nothing. Reply with only NOTHING_TO_SAY; your reply is not shown.", ignore))
        except Exception as e:
            log.warning("saving the cleared thread for %s failed: %s", pid, e)
        finally:
            self.record_usage(session, pid, "job", job="custom")
            try:
                await session.close()
            except Exception:
                pass
        self.emit("project.updated", project=pid)


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
            # Where they asked for it, a line that opens it: an agent Ken made is one tap away, not a hunt in the list.
            self.log_message(HOME, "ken", f'{a["name"]} now has its own chat.', agent_made=a["id"])
            self._bg.append(asyncio.create_task(self.setup_agent(a["id"])))
            return
        if kind == "stop":
            self._bg.append(asyncio.create_task(self.stop_run(str(req.get("run", "")), by="Ken")))
            return
        if kind == "message":  # Ken passing something on: it lands in the agent's chat, and a line here opens it
            aid = self.find_agent(str(req.get("agent", "")))
            text = str(req.get("text", "")).strip()
            if not aid or not text:
                raise ValueError("a message needs an agent and some text")
            name = self.get_agent(aid)["name"]
            self.log_message(aid, "you", text, via="ken")
            self.log_message(HOME, "ken", f"Sent to {name}.", agent_made=aid)
            self._bg.append(asyncio.create_task(self.send_message(aid, text, log_user=False, prompt=(
                f"(From Ken, their chief of staff, passing on what they asked in Ken's chat:)\n{text}"))))
            return
        pid = str(req.get("agent", req.get("project", ""))) or HOME
        if kind == "run" and pid.lower() == "ken":
            pid = HOME
        if pid != HOME:
            pid = self.find_agent(pid) or pid
            self.get_agent(pid)
        if kind == "library_show":  # Ken showing the photos or clips it picked from their Library, in the chat
            items = clean_library(req.get("items"))
            if items:
                self.log_message(pid, "ken", str(req.get("text", ""))[:2000], library=items, via="library")
            return
        if kind == "run":
            waiting = sum(1 for r in self.list_runs(project=HOME, active=True) if r["status"] == "queued")
            if pid == HOME and waiting >= 2 * (os.cpu_count() or 1):
                raise ValueError("too many background jobs waiting already")
            self.start_run(pid, str(req.get("title") or "Task")[:120], str(req["prompt"]),
                           source="ken" if pid == HOME else "agent")
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

    def start_run(self, pid: str, title: str, prompt: str, source: str = "you", origin: int | None = None) -> dict:
        if pid != HOME:
            self.get_agent(pid)
        if self.is_off():
            raise ValueError("Ken is off. Turn it on first.")
        rid = uuid.uuid4().hex[:10]
        if origin or self.chat_origin:
            self.origins[rid] = origin or self.chat_origin
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

    @contextlib.asynccontextmanager
    async def _room(self, rid: str):
        """Wait until the computer has room for this job, read just before it starts: light jobs run side by side, a
        heavy one makes the next wait. The first always starts; after each start the next waits a moment, so its
        reading includes the job just started."""
        while True:
            running = sum(1 for r in self.list_runs(active=True, limit=1000) if r["status"] == "running")
            settled = time.monotonic() - self._last_start >= ROOM_CHECK
            if self._run_row(rid)["status"] == "stopped" or not running or (settled and usage.room_for_more(running)):
                break
            await asyncio.sleep(ROOM_CHECK)
        self._last_start = time.monotonic()
        yield

    async def _execute(self, rid: str) -> None:
        """Run a background job. Whatever goes wrong, it never stays "running" with nobody told: a failure before the
        job even got going (setting up its skills or its AI session) still ends it, with one plain line."""
        try:
            await self._execute_run(rid)
        except Exception as e:
            log.warning("run %s broke before it could finish: %s", rid, e)
            crash.report(e, f"run failed to start: {e}", where="engine._execute")
            run = self._run_row(rid)
            if run["status"] not in ("queued", "running"):
                return  # it had already finished; only what came after went wrong
            pid = run["project"]
            reason = self.explain(e, pid)[0]
            self._set_run(rid, status="failed", finished=time.time(), summary=f"Failed: {reason}", activity="")
            self.emit("run.finished", project=pid, run=rid, status="failed", title=run["title"], summary=f"Failed: {reason}",
                      origin=self.origins.pop(rid, None))
            text = f'■ Your background job "{self._CARRIED.sub("", run["title"])}" didn\'t finish. {reason} Ask me to try again.'
            self.log_message(pid, "ken", text, **({"via": "background"} if pid == HOME else
                                                  {"run": rid, "run_title": run["title"], "run_status": "failed"}))

    async def _execute_run(self, rid: str) -> None:
        run = self._run_row(rid)
        pid = run["project"]
        async with self._room(rid):
            if self._run_row(rid)["status"] == "stopped":
                return
            self._set_run(rid, status="running", started=time.time(), activity="starting")
            self.emit("run.started", project=pid, run=rid)
            cwd = self.home_cwd if pid == HOME else self.workdir(self.get_agent(pid))
            self.sync_skills()
            if self.brain_for(pid).name == "codex":
                self._share_skills(cwd)
            note = (f'\n\nBackground run: "{run["title"]}". Nobody is watching; '
                    "don't ask questions. End with 1-3 lines on what happened, with links.")
            if pid == HOME:
                note = (f'\n\n=== You are a background job: "{run["title"]}" ===\nKen handed you this job so the chat stays '
                        "free; the brief is the message below. Do the work yourself, start to finish. Nobody is watching, so "
                        "don't ask questions: make sensible choices and say what you chose. Don't start other background jobs, "
                        "don't message them or send them files; Ken tells them the result and sends what you made. Things that need their OK "
                        "(sending, posting, spending) you prepare and leave for Ken to ask about. Save what you make in your "
                        "working folder. End with a short report for Ken: what you did, what you chose, and the full path "
                        "of every file you made.")
            if back := self.back_from_credits():
                self.log_message(pid, "ken", back)
            new = lambda resume=None: self.brain_for(pid).session(
                cwd=str(cwd),
                resume=resume,
                system=lambda: self.system(pid) + note,
                model_fn=lambda: self.model_for(pid),
                mcp_fn=lambda: self.agent_tools(pid),
                env_fn=lambda: self._session_env(pid),
            )
            session = new()
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

            status, summary, began, reason, out_of_time = "failed", "", time.monotonic(), "", False
            try:
                backend, done = self.backend(pid), []
                try:
                    final = await asyncio.wait_for(until_idle(session, session.ask(
                        run["prompt"], lambda t: _note(done, t, on_text), lambda t: _note(done, t, on_tool))), RUN_LIMIT)
                except Exception as e:
                    if (isinstance(e, asyncio.TimeoutError) and not isinstance(e, NoAnswer)) or not (switched := self.switch_to_credits(pid)):
                        raise
                    self.log_message(pid, "ken", switched)
                    session = await self._on_credits(session, pid, "background", backend, new, done)
                    self.run_sessions[rid] = session
                    final = await asyncio.wait_for(until_idle(session, session.ask(
                        self._ask_on_credits(run["prompt"], backend, pid, done), on_text, on_tool)), RUN_LIMIT)
                status, summary = "done", self.mask(pid, (final or last or "Finished.").strip())
            except asyncio.CancelledError:
                status, summary = "stopped", (last or "Stopped.").strip()
            except Exception as e:
                log.warning("run %s failed: %s", rid, e)
                if isinstance(e, NoAnswer) or not isinstance(e, asyncio.TimeoutError):
                    reason = self.explain(e, pid)[0]
                    summary = f"Failed: {reason}"
                else:
                    out_of_time = time.monotonic() - began >= RUN_LIMIT - 60
                    reason = "It hit the one-hour limit." if out_of_time else "Ken went quiet for 45 minutes."
                    summary = ("Stopped: it hit the one-hour limit." if out_of_time else "Stopped: Ken went quiet for 45 minutes.") + (f" Last thing it said: {last.strip()[:1500]}" if last.strip() else "")
            finally:
                self.run_sessions.pop(rid, None)
                self.record_usage(session, pid, "background")
                try:
                    await session.close()
                except Exception:
                    pass
            row = self._run_row(rid)
            restarted = status == "stopped" and row["status"] != "stopped" and self.stopping  # Ken shut down under it, not them
            if row["status"] == "stopped":
                status = "stopped"
            self._set_run(rid, status=status, finished=row["finished"] if status == "stopped" and row["finished"] else time.time(),
                          summary=summary[:4000], activity="")
            if restarted:  # say so where they asked, as a stop by them would: a job never just vanishes
                title = self._CARRIED.sub("", run["title"])
                self.log_message(pid, "ken", f'■ Ken restarted, which stopped the background job "{title}". Ask me to carry on and I\'ll pick up from there.',
                                 **({"via": "background"} if pid == HOME else {"run": rid, "run_title": run["title"], "run_status": "stopped"}))
            origin = self.origins.pop(rid, None)
            carried = status != "stopped" and out_of_time and self._carry_on(run, last, origin)
            # A run that carries on isn't a failure to them: screens and the phone don't show it as one.
            self.emit("run.finished", project=pid, run=rid, status=status, title=run["title"], summary=summary[:600],
                      origin=origin, **({"continued": True} if carried else {}))
            if carried:
                return
            if out_of_time and (hours := self._carried(run["title"]) + 1) > 1:
                reason = f"It worked on it for {hours} hours and still wasn't done."
            if status != "stopped" and pid == HOME:
                # Like a helper reporting back: Ken reads the result and tells them, wherever they are.
                self._bg.append(asyncio.create_task(self._report_run(run, status, summary, reason)))
            elif status != "stopped":
                self.log_message(pid, "ken", summary[:4000], run=rid, run_title=run["title"], run_status=status)

    # A job cut off by the one-hour limit is usually just a big job (a month of posts), not a broken one: it carries on
    # in a fresh run from what it already made, up to this many times, before Ken tells them it didn't finish.
    CARRY_ONS = 2
    _CARRIED = re.compile(r" \(continued(?: (\d+))?\)$")
    _CARRY_NOTE = "\n\n(This job already ran for an hour"

    def _carried(self, title: str) -> int:
        """How many times a job has carried on already, from its title: "Posts (continued 2)" is 2."""
        m = self._CARRIED.search(title)
        return int(m.group(1) or 1) if m else 0

    def _carry_on(self, run: dict, last: str, origin: int | None) -> bool:
        """Start the next run of a job that hit the time limit. False when it has carried on enough already."""
        done = self._carried(run["title"])
        if done >= self.CARRY_ONS:
            return False
        base = self._CARRIED.sub("", run["title"])[:100]
        brief = run["prompt"].split(self._CARRY_NOTE)[0]
        cwd = self.home_cwd if run["project"] == HOME else self.workdir(self.get_agent(run["project"]))
        prompt = (f"{brief}{self._CARRY_NOTE} and was cut off by the time limit before it finished. "
                  + (f"The last thing it said was:\n{last.strip()[:1500]}\n\n" if last.strip() else "")
                  + f"Carry on from where it left off. What it made so far is in your working folder ({cwd}): check what's "
                  "already there and build on it. Don't start over.)")
        try:
            self.start_run(run["project"], base + (" (continued)" if not done else f" (continued {done + 1})"), prompt,
                           source=run["source"], origin=origin)
        except Exception as e:
            log.warning("couldn't carry on with run %s: %s", run["id"], e)
            return False
        return True  # quietly: they hear only how the whole job ends

    async def _report_run(self, run: dict, status: str, summary: str, reason: str) -> None:
        """Ken tells them how a background job went, in its own words. When that turn fails or says nothing (the AI
        is down at that moment too), a plain line still reaches them: a job never ends in silence."""
        heard: list[str] = []

        async def listener(t: str) -> None:
            heard.append(t)

        ok = False
        try:
            ok = await self.send_message(HOME, "", log_user=False, via="background", listener=listener, prompt=(
                f'(Your background job "{run["title"]}" {"finished" if status == "done" else "did not finish"}. Its report:\n'
                f"{summary[:4000]}\n\nTell them the result now in a short message, with links, and send any files "
                "they'd want as you normally do. Don't redo the work.)"))
        except asyncio.CancelledError:
            raise  # Ken is shutting down: the turn is picked up again when it starts (resume_interrupted)
        except Exception as e:
            log.warning("reporting run %s failed: %s", run["id"], e)
        if ok and heard:
            return
        title = self._CARRIED.sub("", run["title"])  # the job they know, not its third run
        if status == "done":
            started = self._run_row(run["id"])["started"]
            made = self.made_since(started) if started else []
            text = f'✓ Your background job "{title}" is done.' + (
                "\nIt made: " + ", ".join(f"`{p}`" for p in made) if made else "") + "\nAsk me if you want the details."
        else:
            text = f'■ Your background job "{title}" didn\'t finish. {reason or "Something went wrong."} Ask me to try again.'
        self.log_message(HOME, "ken", text, via="background")

    # ---- their own plan at its limit: Ken AI credits until it resets ----
    # A reply, job or background job that fails because their own Claude or ChatGPT plan is at its limit switches Ken to
    # Ken AI credits (the same routing as picking them in Settings → AI, but not saved as their choice) and runs again
    # once. When the plan resets, Ken goes back to their own by itself. One line each way.
    def _for_now(self) -> dict:
        return json.loads(self.kv_get("credits-for-now") or "{}")

    def _drop_sessions(self) -> None:
        """Open chats reopen on the AI now in use (a busy one when its reply ends)."""
        for pid, s in list(self.chats.items()):
            if s.busy:
                self.chat_tools.pop(pid, None)
            else:
                self.chats.pop(pid)
                self._bg.append(asyncio.create_task(s.close()))

    def switch_to_credits(self, pid: str) -> str:
        """Their own plan is at its limit (Claude's or ChatGPT's reading says until when), they turned on keeping going
        with Ken AI credits (Settings → AI) and the plan has them: switch until it resets. Returns the line to tell them,
        or "" when Ken didn't switch."""
        from onboarding import included_ai_in_use, included_ai_key, use_included_ai

        backend = self.backend(pid)
        at = self.limit_resets(backend) if self.limit_resets and backend in ("claude", "codex") else ""
        if (not auto_credits() or not at or not included_ai_key() or os.environ.get("KEN_USE_CREDITS") in ("0", "1")
                or (backend == "claude" and included_ai_in_use())):
            return ""
        state = self._for_now()
        state.setdefault("from", backend)
        state["until"] = at
        self.kv_set("credits-for-now", json.dumps(state))
        os.environ["KEN_CREDITS_FOR_NOW"] = "1"
        if not use_included_ai():  # Ken AI credits couldn't be used after all: as it was
            self.db.execute("DELETE FROM kv WHERE k='credits-for-now'")
            os.environ.pop("KEN_CREDITS_FOR_NOW", None)
            return ""
        if backend == "codex":  # Ken AI credits run on Claude: this chat moves to it until then
            state["chats"] = sorted({*state.get("chats", []), pid})
            self.kv_set("credits-for-now", json.dumps(state))
            self.kv_set(f"backend:{pid}", "claude")
        self._drop_sessions()
        self.broadcast("chat.runtime", pid, backend="claude")
        name = "ChatGPT" if backend == "codex" else "Claude"
        return f"Your {name} plan hit its limit, so I'm carrying on with Ken AI credits until {at}."

    async def _on_credits(self, session, pid: str, kind: str, backend: str, new, done: list[str]):
        """A job or background run just switched to Ken AI credits: the session to carry on in. On Claude, the same
        one when it had already said or done something (it saw that); else, or from ChatGPT, a fresh one."""
        self.record_usage(session, pid, kind)
        with contextlib.suppress(Exception):
            await session.close()
        return new(session.session_id if backend == "claude" and done else None)

    def _ask_on_credits(self, ask: str, backend: str, pid: str, done: list[str]) -> str:
        """What a switched job or run is asked on Ken AI credits: the same ask when nothing was done yet, else carry on."""
        name = "ChatGPT" if backend == "codex" else "Claude"
        if not done:
            return ask
        return carry_on(name) if backend == "claude" else ask + "\n\n" + carry_on(name, done)

    def credits_until(self) -> str:
        """While Ken has switched to Ken AI credits by itself: when their own plan resets (Settings shows "until 22:20")."""
        state = self._for_now()
        if not state:
            return ""
        return (self.limit_resets(state["from"]) if self.limit_resets else "") or state.get("until", "")

    def back_from_credits(self, force: bool = False) -> str:
        """Before each reply or job: once their plan has reset (or they picked an AI themselves: `force`), back to their
        own. Returns the line to tell them, or ""."""
        from onboarding import use_included_ai

        state = self._for_now()
        if not state:
            return ""
        if not force and self.limit_resets and self.limit_resets(state["from"]):
            if os.environ.get("KEN_CREDITS_FOR_NOW") != "1":  # Ken restarted meanwhile
                os.environ["KEN_CREDITS_FOR_NOW"] = "1"
                use_included_ai()
            return ""
        self.db.execute("DELETE FROM kv WHERE k='credits-for-now'")
        os.environ.pop("KEN_CREDITS_FOR_NOW", None)
        use_included_ai()
        for pid in state.get("chats", []):
            self.kv_set(f"backend:{pid}", state["from"])
            self.broadcast("chat.runtime", pid, backend=state["from"])
        self._drop_sessions()
        return "" if force else f"Back on your own {'ChatGPT' if state['from'] == 'codex' else 'Claude'}."

    def _own_brain(self) -> str:
        """Their own Claude or ChatGPT that's connected ("claude", "codex"), or ""."""
        from onboarding import own_claude

        if (state := self._for_now()).get("from"):
            return state["from"]
        if own_claude():
            return "claude"
        return "codex" if self.usable_brains and "codex" in self.usable_brains() else ""

    def explain(self, e: BaseException, pid: str, via: str | None = None) -> tuple[str, str]:
        """explain_failure, plus what Ken already knows: when their own plan is at its limit right now, that's the reason
        (Claude's usage readings say so even when the error itself only says it failed), with when it opens again.
        `via`: on Telegram there's no Settings, so it says where Settings is (the site)."""
        from onboarding import included_ai_key

        from onboarding import included_ai_in_use

        msg, action = explain_failure(e, self.backend(pid))
        on_credits = self.backend(pid) == "claude" and included_ai_in_use()
        if action == "credits" and (own := self._own_brain()):
            # Out of Ken AI credits with their own Claude or ChatGPT connected: the way to keep going.
            name = "ChatGPT" if own == "codex" else "Claude"
            if self._for_now() and self.back_from_credits():  # Ken's switch, and their own plan has reset meanwhile
                return f"You've used your Ken AI credits, so I switched back to your own {name}.", "retry"
            at = self.limit_resets(own) if self.limit_resets else ""
            resets = f" (its limit resets at {at})" if at else ""
            return f"You've used your Ken AI credits. Switch back to your own {name} in {settings_ai(via)}{resets}.", action
        at = self.limit_resets(self.backend(pid)) if self.limit_resets and not on_credits else ""
        if at and action == "retry" and "can't use that model" not in msg:
            name = "ChatGPT" if self.backend(pid) == "codex" else "Claude"
            msg = f"You've reached your {name} plan's limit for now. It resets at {at}."
        if "plan's limit" in msg and included_ai_key():  # their plan has Ken AI credits: a way to keep going now
            msg += f" To keep going now, switch to Ken AI credits in {settings_ai(via)}."
        return msg, action

    async def stop_run(self, rid: str, by: str = "") -> None:
        run = self._run_row(rid)
        if run["status"] not in ("queued", "running"):
            return
        self._set_run(rid, status="stopped", finished=time.time())
        if run["project"] == HOME:  # so a promised result isn't left hanging, here or on the phone
            who = {"you": "You stopped", "Ken": "Ken stopped"}.get(by, "Stopped")
            made = self.made_since(run["started"]) if run["started"] else []
            self.log_message(HOME, "ken", f'■ {who} the background job "{run["title"]}".' + (
                "\nIt had made: " + ", ".join(f"`{p}`" for p in made) + "\nAsk Ken to carry on and it picks up from there."
                if made else ""), via="background")
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
            self._set_run(rid, summary="Stopped before it started.")
            self.emit("run.finished", project=run["project"], run=rid, status="stopped", title=run["title"], summary="Stopped.")

    def made_since(self, since: float, most: int = 4) -> list[str]:
        """The newest files in Ken's working folder changed since `since`: what a stopped job got done."""
        found, seen = [], 0
        for root, dirs, files in os.walk(self.home_cwd):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
            for name in files:
                seen += 1
                if name.startswith(".") or seen > 20000:
                    continue
                path = Path(root) / name
                try:
                    changed = path.stat().st_mtime
                except OSError:
                    continue
                if changed >= since:
                    found.append((changed, path))
        found.sort(reverse=True)
        return [p.relative_to(self.home_cwd).as_posix() for _, p in found[:most]]

    # ------------------------------------------------------------ approvals

    def create_approval(self, pid: str, kind: str, title: str, body: str, next_step: str = "") -> dict:
        aid = uuid.uuid4().hex[:10]
        self.db.execute(
            "INSERT INTO approvals (id, project, kind, title, body, next, status, created) VALUES (?,?,?,?,?,?,?,?)",
            (aid, pid, kind if kind in APPROVAL_KINDS else "other", title.strip()[:200], body.strip()[:20000],
             next_step.strip()[:2000], "pending", time.time()),
        )
        a = self.get_approval(aid)
        if self.chat_origin:
            self.origins[aid] = self.chat_origin
        self.emit("approval.created", project=pid, approval=a, origin=self.chat_origin)
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
        origin = self.origins.pop(aid, None)
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
                self.start_run(a["project"], a["title"], go, source="approval", origin=origin)
        return a

    # ------------------------------------------------------------------- views

    def chats_list(self) -> list[dict]:
        """Ken first, then each project — like a chat app's list."""
        rows = [(HOME, self.home_name(), "#1B1A17", "")] + [
            (p["id"], p["name"], p["color"], picture(p)) for p in self.list_agents()]
        out = []
        for pid, name, color, emoji in rows:
            last = self.messages(pid, 1)
            preview = ""
            if last:
                preview = plain_text(last[0]["text"] or "") or ("📎 " + ", ".join(last[0].get("files") or []))
            out.append({"id": pid, "name": name, "color": color, "emoji": emoji, "last": preview[:120],
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
            # Finished jobs stay in their chat as a card (done, stopped or didn't finish), so a job never just disappears.
            "done": [{k: v for k, v in r.items() if k != "prompt"} for r in self.list_runs(active=False, since=time.time() - 30 * 86400, limit=50)],
            "home": self.messages(HOME, 30),
            "home_busy": self.chat_busy(HOME),
        }

    # ------------------------------------------------------------------ voice

    # Voice mode: a fast talker (a voice service the hosting site connects) speaks for Ken, and hands real work to
    # Ken itself. What it knows at the start of each call comes from here: small, so it stays fast, and only things
    # fit to say out loud (no keys, tokens or file paths).
    def voice_context(self) -> dict:
        def part(text: str, limit: int) -> str:
            text = speakable(text).strip()
            return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0].rstrip() + " …"

        try:
            soul = self.context_file(HOME).read_text(errors="replace")
        except OSError:
            soul = ""
        name = self.home_name()
        # About them: the notes that look like they're about the person, then what each other note is about.
        about, d = [], self.memory_dir(HOME)
        notes = sorted(d.glob("*.md")) if d.is_dir() else []
        personal = [f for f in notes if re.search(r"about|profile|people|family|person|me\b|work|voice", f.stem, re.I)]
        for f in personal[:3]:
            about.append(f.read_text(errors="replace").strip())
        listing = [next((ln.strip().lstrip("#").strip() for ln in f.read_text(errors="replace").splitlines() if ln.strip()), "")
                   for f in notes if f not in personal]
        if listing := [ln for ln in listing if ln]:
            about.append("Other things in memory: " + "; ".join(listing[:15]))
        # Today: this morning's brief, if Ken sent one today.
        recent = self.messages(HOME, 60)
        start_of_day = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        brief = next((m["text"] for m in reversed(recent) if m.get("role") == "ken" and m.get("ts", 0) >= start_of_day
                      and (m.get("job") or {}).get("name") == "morning-brief" and m.get("text")), "")
        chat = [f"{'Them' if m['role'] == 'you' else name}: {' '.join(str(m['text']).split())[:280]}"
                for m in recent if m.get("role") in ("you", "ken") and m.get("text") and not m.get("call")][-8:]
        return {
            "ken_name": name[:40],
            "soul": part(soul, 3000),
            "about_you": part("\n\n".join(about), 1500),
            "today": part(brief, 1000),
            "recent_chat": part("\n".join(chat), 1800),
            "now": _day_and_time(),
        }

    ONBOARDING_KEYS = ("call_name", "work", "busy", "apps", "brief", "ken_name", "style")

    def apply_onboarding(self, facts: dict) -> list[str]:
        """What they told the hosting site about themselves while their computer was set up (each a few plain words).
        Only their name for Ken is written at once (its chat shows it). The rest, Ken's next turn is told, and Ken files
        what's worth keeping where it belongs (soul file or memory), as with anything else it learns. Returns the getting-to-know items it answers."""
        def clean(key: str, limit: int = 300) -> str:
            value = facts.get(key)
            value = ", ".join(map(str, value)) if isinstance(value, list) else str(value or "")
            return " ".join(speakable(value).split())[:limit].strip()

        call, work, busy, apps, brief, style = (clean(k) for k in ("call_name", "work", "busy", "apps", "brief", "style"))
        ken = re.sub(r"[^\w .'-]", "", clean("ken_name", 40)).strip()
        covered = [item for item, value in (("ken-name", ken), ("style", style), ("call-you", call), ("work", work), ("tools", apps),
                                            ("brief-time", brief)) if value]
        said = "; ".join(f"{k}: {v}" for k, v in (("call them", call), ("your name", ken), ("their work", work),
                         ("would like help with", busy), ("apps", apps), ("morning brief", brief), ("how to talk to them", style)) if v)
        if ken:  # their name for Ken shows on its chat at once, before Ken's first turn files the rest
            soul = self.context_file(HOME)
            try:
                lines = soul.read_text().splitlines(keepends=True)
                if lines and re.match(r"#\s*You are\b", lines[0]):
                    soul.write_text(f"# You are {ken}\n" + "".join(lines[1:]))
            except OSError:
                pass
        if said:
            self.kv_set(f"onboarding:said:{HOME}", said)
        # A Home chat already open: reopen it (it resumes the same conversation), so the next reply gets the note.
        s = self.chats.get(HOME)
        if s is not None and not s.busy:
            self.chats.pop(HOME)
            self._bg.append(asyncio.create_task(s.close()))
        self.emit("project.updated", project=HOME)
        return covered

    def remember(self, fact: str) -> str:
        """Something said on a voice call that's worth keeping, into Ken's memory (the nightly review files it)."""
        fact = " ".join(speakable(fact).split())[:300]
        if not fact:
            raise ValueError("Nothing to remember.")
        f = self.memory_dir(HOME) / "voice.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text("Things they told me on voice calls (to file into the right notes)\n\n")
        with f.open("a") as out:
            out.write(f"- {fact} ({_short_date()})\n")
        return fact

    def voice_call_done(self, lines: list[dict], seconds: float) -> dict | None:
        """After a voice call, its words go into the Home chat, so voice and text are one history."""
        def clean(text) -> str:  # without the voice's delivery cues ("[excited] Oh, nice!")
            return " ".join(re.sub(r"\[[a-z][a-z ]{1,24}\]", " ", str(text), flags=re.I).split())[:1000]
        said = [(str(x.get("role")), clean(x.get("text", ""))) for x in lines[:200]
                if isinstance(x, dict) and x.get("role") in ("you", "ken") and clean(x.get("text", ""))]
        if not said:
            return None
        text = "\n".join(f"{'You' if role == 'you' else 'Ken'}: {words}" for role, words in said)
        self.kv_set(f"voice:said:{HOME}", " | ".join(f"{'they' if role == 'you' else 'you'}: {words}" for role, words in said)[-3000:])
        return self.log_message(HOME, "ken", text[:20000], call={"seconds": round(seconds)}, via="voice")

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
        name = self.home_name() if pid == HOME else self.get_agent(pid)["name"]
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
