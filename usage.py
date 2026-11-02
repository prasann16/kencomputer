"""Usage: how much of the AI plan is used, and how much of this computer's memory, for /usage, a
heads-up before the plan runs out, and one when the computer is running out of memory.

Claude Code reports the plan's usage windows (5 hour, weekly) as rate limit events, only when they
change; Codex reports the ChatGPT plan's after each turn. Ken keeps the latest reading of each window
in usage.json. When a window gets near its limit, Ken says so once, with when it resets, so running
out never comes as a surprise.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("ken")

WINDOW_NAMES = {"five_hour": "5 hour", "seven_day": "weekly", "monthly": "monthly", "seven_day_opus": "weekly Opus",
                "seven_day_sonnet": "weekly Sonnet", "overage": "extra usage"}
PLAN_NAMES = {"claude": "Claude", "codex": "ChatGPT"}
WARN_AT = 80  # % of a window used when Ken gives the heads-up
LOW_MEMORY_FOR = 120  # seconds memory must stay critical before Ken says so (a short spike isn't news)


# ------------------------------------------------------------------ the plan

def readings_from(raw: dict, now: float | None = None) -> dict[str, dict]:
    """One Claude rate limit event as {window: {pct, resets_at, status, at}}. The windows' utilization comes
    in raw["unifiedWindows"] (the SDK's own utilization field stays empty); status is for rateLimitType."""
    now = time.time() if now is None else now
    out: dict[str, dict] = {}
    for name, w in (raw.get("unifiedWindows") or {}).items():
        if isinstance(w, dict):
            out[name] = {"pct": _pct(w.get("utilization")), "resets_at": w.get("resetsAt"), "at": now}
    kind = raw.get("rateLimitType")
    if kind:
        w = out.setdefault(kind, {"pct": None, "resets_at": None, "at": now})
        w["status"] = raw.get("status")
        if raw.get("resetsAt"):
            w["resets_at"] = raw["resetsAt"]
        if w["pct"] is None:
            w["pct"] = _pct(raw.get("utilization"))
    return out


def codex_readings(snapshot: dict, now: float | None = None) -> dict[str, dict]:
    """Codex's rateLimits (a primary and a secondary window, each usedPercent and resetsAt) the same way."""
    now = time.time() if now is None else now
    out: dict[str, dict] = {}
    for key in ("primary", "secondary"):
        w = snapshot.get(key)
        if not isinstance(w, dict) or w.get("usedPercent") is None:
            continue
        mins = w.get("windowDurationMins") or 0
        name = ({300: "five_hour", 10080: "seven_day", 43200: "monthly"}.get(mins)
                or (f"{mins // 60} hour" if mins < 2880 else f"{mins // 1440} day") if mins else key)
        out[name] = {"pct": float(w["usedPercent"]), "resets_at": w.get("resetsAt"), "at": now}
        if snapshot.get("rateLimitReachedType") and w["usedPercent"] >= 100:
            out[name]["status"] = "rejected"
    return out


def _pct(v) -> float | None:
    return round(float(v) * 100, 1) if isinstance(v, (int, float)) else None


def near_limit(w: dict, now: float) -> bool:
    """Close enough to the limit to say so; a window past its reset no longer counts."""
    if w.get("resets_at") and w["resets_at"] <= now:
        return False
    return w.get("status") in ("allowed_warning", "rejected") or (w.get("pct") or 0) >= WARN_AT


def when(ts: float | None, now: float | None = None) -> str:
    """A reset time as they'd say it: '22:09' today, 'Fri 19:50' this week, 'Nov 23' later."""
    if not ts:
        return "unknown"
    now = time.time() if now is None else now
    t = time.localtime(ts)
    if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d", time.localtime(now)):
        return time.strftime("%H:%M", t)
    return time.strftime("%a %H:%M" if ts - now < 6 * 86400 else "%b %-d" if os.name != "nt" else "%b %#d", t)


def bar(pct: float, width: int = 10) -> str:
    """A text meter for Telegram, which has no real progress bars: ▰▰▰▱▱▱▱▱▱▱ 30%."""
    filled = max(0, min(width, round(pct / 100 * width)))
    return "▰" * filled + "▱" * (width - filled) + f" {pct:.0f}%"


# ------------------------------------------------------------------ this computer

def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def memory() -> dict:
    """This computer's RAM in bytes: total and available. Empty where unknown."""
    try:
        if sys.platform.startswith("linux"):
            kb = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, rest = line.partition(":")
                if rest.split():
                    kb[key] = int(rest.split()[0]) * 1024
            return {"total": kb["MemTotal"], "available": kb.get("MemAvailable", kb.get("MemFree", 0))}
        if sys.platform == "darwin":
            total = int(_run("sysctl", "-n", "hw.memsize").strip() or 0)
            out = _run("vm_stat")
            page = int(out.split("page size of")[1].split()[0]) if "page size of" in out else 4096
            pages = {k.strip(): int(v.strip().rstrip(".")) for k, _, v in
                     (line.partition(":") for line in out.splitlines()[1:]) if v.strip().rstrip(".").isdigit()}
            free = sum(pages.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
            return {"total": total, "available": free * page} if total else {}
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("available", ctypes.c_ulonglong)] + [(f"x{i}", ctypes.c_ulonglong) for i in range(5)]
            s = Status()
            s.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
                return {"total": s.total, "available": s.available}
    except (OSError, ValueError, KeyError, IndexError, AttributeError):
        pass
    return {}


def ken_memory(root: int | None = None) -> int:
    """RAM used by Ken: this process and everything it started (chats, tools, browsers), in bytes.
    0 where `ps` isn't there (Windows)."""
    rss, kids = {}, {}
    for line in _run("ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "rss=").splitlines():
        parts = line.split()
        if len(parts) == 3 and all(x.isdigit() for x in parts):
            pid, ppid, kb = map(int, parts)
            rss[pid] = kb * 1024
            kids.setdefault(ppid, []).append(pid)
    total, todo = 0, [root or os.getpid()]
    while todo:
        pid = todo.pop()
        total += rss.get(pid, 0)
        todo += kids.get(pid, [])
    return total


def memory_critical() -> bool | None:
    """Is this computer out of memory right now? On a Mac, macOS's own memory pressure is red (the
    graph in Activity Monitor); elsewhere, under 5% is free. None where it can't tell. Microseconds."""
    if sys.platform == "darwin":
        try:
            import ctypes
            import ctypes.util

            level, size = ctypes.c_int(), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            if libc.sysctlbyname(b"kern.memorystatus_vm_pressure_level", ctypes.byref(level), ctypes.byref(size), None, 0) == 0:
                return level.value >= 4  # 1 normal, 2 warning, 4 critical
        except (OSError, AttributeError):
            pass
        return None
    mem = memory()
    return 100 * mem["available"] / mem["total"] < 5 if mem.get("total") else None


def app_name(command: str) -> str:
    """The name someone would know a program by: the app it belongs to ('Google Chrome' for its
    helpers), else the program's name, without a reverse-DNS prefix ('VirtualMachine')."""
    app = next((part[:-4] for part in command.split("/") if part.endswith(".app")), None)
    if app:
        return app
    name = os.path.basename(command).removesuffix(".exe")
    return name.rsplit(".", 1)[-1] if name.count(".") >= 2 and " " not in name else name


def biggest_app() -> tuple[str, int]:
    """The program using the most memory, all its processes added up (a browser's helpers count as
    the browser), leaving out Ken's own: ('Google Chrome', bytes). ('', 0) where `ps` isn't there."""
    mine = set()
    rows = []
    for line in _run("ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "rss=", "-o", "comm=").splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
            rows.append((int(parts[0]), int(parts[1]), int(parts[2]) * 1024, parts[3]))
    kids: dict[int, list[int]] = {}
    for pid, ppid, _, _ in rows:
        kids.setdefault(ppid, []).append(pid)
    todo = [os.getpid()]
    while todo:
        pid = todo.pop()
        mine.add(pid)
        todo += kids.get(pid, [])
    by: dict[str, int] = {}
    for pid, _, rss, comm in rows:
        if pid in mine:
            continue
        by[app_name(comm)] = by.get(app_name(comm), 0) + rss
    return max(by.items(), key=lambda kv: kv[1], default=("", 0))


def gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f}GB"


# ------------------------------------------------------------------ readings and /usage

class Usage:
    """The latest plan readings and which heads-ups Ken has already given, in usage.json."""

    def __init__(self, home: Path, say: Callable[[str], None] = lambda text: None,
                 jobs: Callable[[], list[str]] = lambda: []) -> None:
        self.path = Path(home) / "usage.json"
        self.say = say  # one message to them, in the app and on the phone
        self.jobs = jobs  # Ken's background jobs running now, by name
        self._low_since: float | None = None  # when memory went critical, while it is

    def load(self) -> dict:
        try:
            state = json.loads(self.path.read_text())
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.path)

    def record(self, raw: dict, now: float | None = None) -> None:
        """A Claude rate limit event from any session."""
        self._keep("claude", readings_from(raw, now), now)

    def record_codex(self, snapshot: dict, now: float | None = None) -> None:
        """Codex's reading of the ChatGPT plan, after a turn or for /usage."""
        self._keep("codex", codex_readings(snapshot, now), now)

    def _keep(self, plan: str, readings: dict[str, dict], now: float | None) -> None:
        """Keep each window's newest reading, and give the heads-up once per window near its limit."""
        now = time.time() if now is None else now
        try:
            state = self.load()
            windows, said = state.setdefault(plan, {}), state.setdefault("said", {})
            for name, w in readings.items():
                old = windows.get(name, {})
                if w.get("status") is None and old.get("resets_at") == w.get("resets_at"):
                    w["status"] = old.get("status")  # the status only comes with the window it's about
                windows[name] = w = {k: v for k, v in w.items() if v is not None}
                if near_limit(w, now) and said.get(f"{plan}:{name}") != w.get("resets_at"):
                    said[f"{plan}:{name}"] = w.get("resets_at")
                    used = f"{w['pct']:.0f}%" if w.get("pct") is not None else "nearly all"
                    window = WINDOW_NAMES.get(name, name)
                    self.say(f"Heads-up: you've used {used} of your {PLAN_NAMES[plan]} plan's {window} limit. "
                             f"It resets {when(w.get('resets_at'), now)}.")
            self.save(state)
        except Exception as e:
            log.warning("couldn't save the usage reading: %s", e)

    def report(self, backend: str, now: float | None = None, mem: dict | None = None, ken: int | None = None) -> str:
        """/usage: each window's meter and when it resets, then this computer's memory."""
        now = time.time() if now is None else now
        plan = PLAN_NAMES.get(backend, backend)
        windows = self.load().get(backend, {})
        lines = [f"Your {plan} plan:"]
        if not windows:
            lines.append(f"No reading yet. {plan} reports it as you chat, so ask again after a reply.")
        for name in sorted(windows, key=lambda k: list(WINDOW_NAMES).index(k) if k in WINDOW_NAMES else 99):
            w = windows[name]
            label = WINDOW_NAMES.get(name, name).capitalize()
            if w.get("resets_at") and w["resets_at"] <= now:
                lines.append(f"{label}: reset at {when(w['resets_at'], now)}")
                continue
            used = bar(w["pct"]) if w.get("pct") is not None else (w.get("status") or "no %").replace("_", " ")
            lines.append(f"{label}  {used} · resets {when(w.get('resets_at'), now)}")
        mem = memory() if mem is None else mem
        if mem.get("total"):
            used = mem["total"] - mem["available"]
            lines += ["", "This computer:", f"Memory  {bar(100 * used / mem['total'])} · {gb(used)} of {gb(mem['total'])}"]
            ken = ken_memory() if ken is None else ken
            if ken:
                lines.append(f"Ken is using {gb(ken)} of it")
        return "\n".join(lines)

    # -- running out of memory

    def low_memory(self, critical: bool | None, now: float | None = None) -> bool:
        """Whether to say it now: memory critical for LOW_MEMORY_FOR seconds, and not said yet today."""
        now = time.time() if now is None else now
        if not critical:
            self._low_since = None
            return False
        self._low_since = self._low_since or now
        if now - self._low_since < LOW_MEMORY_FOR:
            return False
        state, today = self.load(), time.strftime("%Y-%m-%d", time.localtime(now))
        if state.get("memory_said") == today:
            return False
        state["memory_said"] = today
        self.save(state)
        return True

    def low_memory_text(self, app: tuple[str, int], ken: int, jobs: list[str]) -> str:
        computer = "Mac" if sys.platform == "darwin" else "computer"
        if not (ken or app[1]):  # no `ps` (Windows): who's using it is unknown
            return f"Your {computer} is running out of memory. Closing some apps or windows will help."
        if ken >= app[1]:
            what = f' for the background job "{jobs[0]}"' if len(jobs) == 1 else " for background jobs" if jobs else ""
            return (f"Your {computer} is running out of memory, and Ken is using {gb(ken)} of it{what}. "
                    "Say stop if you want me to stop.")
        return (f"Your {computer} is running out of memory. {app[0]} is using {gb(app[1])}. "
                "Closing it, or some of its windows, will help.")

    async def watch_memory(self, every: float = 60) -> None:
        """Once a minute, a microsecond check; the slower look at who's using memory only when it's needed."""
        while True:
            try:
                if self.low_memory(memory_critical()):
                    app, ken = await asyncio.to_thread(biggest_app), await asyncio.to_thread(ken_memory)
                    text = self.low_memory_text(app, ken, self.jobs())
                    log.warning("%s", text)
                    self.say(text)
            except Exception as e:
                log.warning("memory check failed: %s", e)
            await asyncio.sleep(every)
