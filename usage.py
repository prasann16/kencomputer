"""Usage: how much of the AI plan and of this computer Ken is using, and when to ease off.

Claude Code reports the plan's usage windows (5 hour, weekly) as rate limit events, only when they
change, never on a timer. Ken keeps the latest reading of each window in usage.json and checks it,
with this computer's RAM and load, once a minute. Near a limit it says so once; at the limit, or
when the computer is struggling, background jobs and scheduled jobs wait until it's over.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("ken")

WINDOW_NAMES = {"five_hour": "5 hour", "seven_day": "weekly", "seven_day_opus": "weekly Opus",
                "seven_day_sonnet": "weekly Sonnet", "overage": "extra usage"}
PRESSURE_FOR = 180  # seconds a strain (or its end) must last before Ken acts on it
RAM_LOW = 10  # % of RAM available under which the computer is under pressure
LOAD_HIGH = 2.0  # 5 minute load average per core above which it is
EXEMPT_JOBS = ("morning-brief",)  # still runs when Ken eases off: it's the one they read every day


def thresholds() -> tuple[float, float]:
    """Warn and pause points, in % of a window; KEN_USAGE_WARN and KEN_USAGE_PAUSE in .env change them."""
    def num(name, default):
        try:
            return float(os.environ.get(name, default))
        except ValueError:
            return float(default)
    return num("KEN_USAGE_WARN", 80), num("KEN_USAGE_PAUSE", 90)


# ------------------------------------------------------------------ AI plan

def readings_from(raw: dict, now: float | None = None) -> dict[str, dict]:
    """One rate limit event as {window: {pct, resets_at, status, at}}. The windows' utilization comes
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


def _pct(v) -> float | None:
    return round(float(v) * 100, 1) if isinstance(v, (int, float)) else None


def level(w: dict, warn: float, pause: float, now: float) -> str:
    """'pause', 'warn' or '' for one window; a window past its reset no longer counts."""
    if w.get("resets_at") and w["resets_at"] <= now:
        return ""
    pct, status = w.get("pct"), w.get("status")
    if status == "rejected" or (pct is not None and pct >= pause):
        return "pause"
    if status == "allowed_warning" or (pct is not None and pct >= warn):
        return "warn"
    return ""


def when(ts: float | None, now: float | None = None) -> str:
    """A reset time as they'd say it: '22:09' today, 'Fri 19:50' later."""
    if not ts:
        return "unknown"
    now = time.time() if now is None else now
    t = time.localtime(ts)
    return time.strftime("%H:%M" if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d", time.localtime(now))
                         else "%a %H:%M", t)


def ago(seconds: float) -> str:
    m = int(seconds // 60)
    return "just now" if m < 1 else f"{m} min ago" if m < 60 else f"{m // 60} h ago" if m < 48 * 60 else f"{m // 1440} days ago"


# ------------------------------------------------------------------ this computer

def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def cores() -> int:
    return os.cpu_count() or 1


def load() -> tuple[float, float, float] | None:
    try:
        return os.getloadavg()
    except (OSError, AttributeError):  # Windows has no load average
        return None


def memory() -> dict:
    """RAM and swap in bytes: total, available, swap_total, swap_used. Empty where unknown."""
    if sys.platform.startswith("linux"):
        kb = {}
        for line in _read("/proc/meminfo").splitlines():
            key, _, rest = line.partition(":")
            if rest.split():
                kb[key] = int(rest.split()[0]) * 1024
        if "MemTotal" not in kb:
            return {}
        return {"total": kb["MemTotal"], "available": kb.get("MemAvailable", kb.get("MemFree", 0)),
                "swap_total": kb.get("SwapTotal", 0), "swap_used": kb.get("SwapTotal", 0) - kb.get("SwapFree", 0)}
    if sys.platform == "darwin":
        total = int(_run("sysctl", "-n", "hw.memsize").strip() or 0)
        if not total:
            return {}
        out = _run("vm_stat")
        page = int(out.split("page size of")[1].split()[0]) if "page size of" in out else 4096
        pages = {k.strip(): int(v.strip().rstrip(".") or 0) for k, _, v in
                 (line.partition(":") for line in out.splitlines()[1:]) if v.strip().rstrip(".").isdigit()}
        free = sum(pages.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
        swap = _run("sysctl", "-n", "vm.swapusage").split()  # total = 2048.00M  used = 1.25M  free = ...
        mb = lambda key: float(swap[swap.index(key) + 2].rstrip("M")) * 1024 * 1024 if key in swap else 0  # noqa: E731
        return {"total": total, "available": free * page, "swap_total": mb("total"), "swap_used": mb("used")}
    return {}


def cpu_times() -> tuple[int, int] | None:
    """(busy, total) CPU ticks since boot on Linux, for a % between two readings."""
    first = _read("/proc/stat").split("\n", 1)[0].split()
    if not first or first[0] != "cpu":
        return None
    ticks = [int(x) for x in first[1:]]
    idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
    return sum(ticks) - idle, sum(ticks)


async def cpu_percent() -> float | None:
    a = cpu_times()
    if a is None:
        out = _run("ps", "-A", "-o", "%cpu=")  # macOS: what every process is using now
        try:
            return round(sum(float(x) for x in out.split()) / cores(), 0) if out else None
        except ValueError:
            return None
    await asyncio.sleep(0.5)
    b = cpu_times()
    return round(100 * (b[0] - a[0]) / max(1, b[1] - a[1]), 0)


def top_memory(n: int = 3) -> list[tuple[str, int]]:
    """The programs using the most RAM, every process of one program added up: [('chromium', bytes)]."""
    by: dict[str, int] = {}
    for line in _run("ps", "-A", "-o", "rss=", "-o", "comm=").splitlines():
        rss, _, name = line.strip().partition(" ")
        if rss.isdigit() and name.strip():
            name = os.path.basename(name.strip())
            by[name] = by.get(name, 0) + int(rss) * 1024
    return sorted(by.items(), key=lambda kv: -kv[1])[:n]


def cpu_model() -> str:
    if sys.platform.startswith("linux"):
        for line in _read("/proc/cpuinfo").splitlines():
            if line.startswith("model name"):
                return " ".join(line.split(":", 1)[1].split())
    if sys.platform == "darwin":
        return _run("sysctl", "-n", "machdep.cpu.brand_string").strip()
    return platform.processor()


def os_name() -> str:
    if sys.platform.startswith("linux"):
        for line in _read("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return f"{platform.system()} {platform.release()}"


def gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f}GB"


def strain(mem: dict, avg: tuple | None, n: int, *, easing: bool = False) -> str:
    """Why the computer is under pressure ('RAM 95%, load 12 on 4 cores'), or '' when it's fine.
    Once under pressure (easing=True) it only counts as over with some room to spare."""
    ram_low, load_high = (RAM_LOW * 1.5, LOAD_HIGH * 0.75) if easing else (RAM_LOW, LOAD_HIGH)
    ram_hit = bool(mem.get("total")) and 100 * mem["available"] / mem["total"] < ram_low
    load_hit = avg is not None and avg[1] > load_high * n
    if not (ram_hit or load_hit):
        return ""
    used = f"RAM {round(100 - 100 * mem['available'] / mem['total'])}%" if mem.get("total") else ""
    busy = f"load {avg[1]:.0f} on {n} cores" if avg is not None else ""
    return ", ".join(x for x in (used, busy) if x)


# ------------------------------------------------------------------ the watcher

class Usage:
    """The latest plan readings and what Ken has already said about them, in usage.json."""

    def __init__(self, home: Path, say: Callable[[str], None] = lambda text: None,
                 runs: Callable[[], tuple[int, int]] = lambda: (0, 0)) -> None:
        self.path = Path(home) / "usage.json"
        self.say = say  # one message to them, in the app and on the phone
        self.runs = runs  # background runs (running, waiting)
        self.pressure = ""  # why the computer is struggling, while it is
        self._since: float | None = None  # when the strain (or its easing) began

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
        """A rate limit event from any Claude session: keep each window's newest reading."""
        log.info("rate limit event: %s", json.dumps(raw))
        try:
            state = self.load()
            windows = state.setdefault("windows", {})
            for name, w in readings_from(raw, now).items():
                old = windows.get(name, {})
                if w.get("status") is None and old.get("resets_at") == w.get("resets_at"):
                    w["status"] = old.get("status")  # the status only comes with the window it's about
                windows[name] = {k: v for k, v in w.items() if v is not None}
            self.save(state)
        except Exception as e:
            log.warning("couldn't save the usage reading: %s", e)

    # -- decisions

    def ai_hold(self, now: float | None = None) -> float:
        """When the plan is at its pause point: the time it resets (0 when it isn't)."""
        now = time.time() if now is None else now
        warn, pause = thresholds()
        ends = [w.get("resets_at") or now + 3600 for w in self.load().get("windows", {}).values()
                if level(w, warn, pause, now) == "pause"]
        return max(ends, default=0)

    def hold(self, now: float | None = None) -> str:
        """Why background runs must wait right now, or '' if they can start."""
        if self.ai_hold(now):
            return "AI usage near the plan limit"
        return "the computer is busy" if self.pressure else ""

    def run_cap(self, slots: int) -> int:
        return 1 if self.pressure else slots

    def skip_job(self, job: dict, now: float | None = None) -> str:
        """'skip' (plan limit: drop today's run), 'wait' (computer busy: run once it clears) or ''."""
        if job.get("name") in EXEMPT_JOBS:
            return ""
        if self.ai_hold(now):
            return "skip"
        return "wait" if self.pressure else ""

    # -- the checks, once a minute

    def check_ai(self, now: float | None = None) -> list[str]:
        """What to tell them about the plan: each warning once per window, and one line when it resets."""
        now = time.time() if now is None else now
        warn, pause = thresholds()
        state = self.load()
        said = state.setdefault("said", {})
        out = []
        for name, w in state.get("windows", {}).items():
            lv, window_id = level(w, warn, pause, now), w.get("resets_at") or 0
            if not lv or said.get(f"{lv}:{name}") == window_id:
                continue
            said[f"{lv}:{name}"] = window_id
            if lv == "warn" and said.get(f"pause:{name}") == window_id:
                continue  # already told them it's paused
            used = f"at {w['pct']:.0f}%" if w.get("pct") is not None else "near the limit"
            window = WINDOW_NAMES.get(name, name.replace("_", " "))
            line = f"AI usage {used} of your {window} window, resets {when(w.get('resets_at'), now)}."
            if lv == "pause":
                line += " Pausing background jobs and scheduled jobs until then. Chatting with me still works."
                state["paused"] = True
            out.append(line)
        if state.get("paused") and not any(level(w, warn, pause, now) == "pause" for w in state.get("windows", {}).values()):
            state["paused"] = False
            out.append("AI usage window has reset. Background and scheduled jobs are back on.")
        self.save(state)
        return out

    def check_pc(self, mem: dict, avg: tuple | None, n: int, now: float | None = None) -> list[str]:
        """Under pressure only once it has lasted PRESSURE_FOR, and back to normal the same way."""
        now = time.time() if now is None else now
        why = strain(mem, avg, n, easing=bool(self.pressure))
        changing = bool(why) != bool(self.pressure)
        if not changing:
            self._since = None
            if why:
                self.pressure = why
            return []
        self._since = self._since or now
        if now - self._since < PRESSURE_FOR:
            return []
        self._since = None
        if why:
            self.pressure = why
            top = top_memory(1)
            hog = f" Biggest: {top[0][0]} {gb(top[0][1])}." if top else ""
            return [f"PC under pressure: {why}. Pausing new background jobs.{hog}"]
        self.pressure = ""
        return ["PC back to normal. Background jobs can start again."]

    async def watch(self, every: float = 60) -> None:
        """The light monitor: two file reads and a sum, once a minute."""
        while True:
            try:
                lines = self.check_pc(memory(), load(), cores()) + self.check_ai()
                for line in lines:
                    log.info("usage: %s", line)
                    self.say(line)
            except Exception as e:
                log.warning("usage check failed: %s", e)
            await asyncio.sleep(every)

    # -- /usage

    def ai_text(self, backend: str, model: str, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        lines = [f"AI: {backend.capitalize()} · {model or 'default model'}"]
        windows = self.load().get("windows", {})
        if not windows:
            lines.append("No plan usage reading yet. Claude reports it when usage changes, so it shows after a reply or two."
                         if backend == "claude" else "No plan usage reading: only Claude reports it.")
            return lines
        seen = 0.0
        for name in sorted(windows, key=lambda k: list(WINDOW_NAMES).index(k) if k in WINDOW_NAMES else 99):
            w = windows[name]
            seen = max(seen, w.get("at", 0))
            label = WINDOW_NAMES.get(name, name.replace("_", " ")).capitalize()
            if w.get("resets_at") and w["resets_at"] <= now:
                lines.append(f"{label}: reset at {when(w['resets_at'], now)}, no new reading since")
                continue
            used = f"{w['pct']:.0f}% used" if w.get("pct") is not None else (w.get("status") or "no %").replace("_", " ")
            lines.append(f"{label}: {used}, resets {when(w.get('resets_at'), now)}")
        lines.append(f"Read at {time.strftime('%H:%M', time.localtime(seen))} ({ago(now - seen)})")
        return lines

    def pc_text(self, *, host: str, os_: str, cpu: str, n: int, avg, cpu_pct, mem: dict,
                top: list[tuple[str, int]]) -> list[str]:
        lines = [f"PC: {host} · {os_}", f"CPU: {cpu or 'unknown'}, {n} cores"]
        busy = [f"Load {avg[0]:.1f} / {avg[1]:.1f} / {avg[2]:.1f}" if avg else "", f"CPU {cpu_pct:.0f}%" if cpu_pct is not None else ""]
        if any(busy):
            lines.append(" · ".join(x for x in busy if x))
        if mem.get("total"):
            line = f"RAM {gb(mem['total'] - mem['available'])} of {gb(mem['total'])} used, {gb(mem['available'])} free"
            if mem.get("swap_total"):
                line += f" · swap {gb(mem['swap_used'])} of {gb(mem['swap_total'])}"
            lines.append(line)
        if top:
            lines.append("Top RAM: " + ", ".join(f"{name} {gb(size)}" for name, size in top))
        running, waiting = self.runs()
        lines.append(f"Background jobs: {running} running, {waiting} waiting")
        return lines

    async def report(self, backend: str, model: str) -> str:
        lines = self.ai_text(backend, model)
        why = self.hold()
        if why:
            lines.append(f"Paused: background jobs wait, {why}.")
        lines.append("")
        lines += self.pc_text(host=socket.gethostname(), os_=os_name(), cpu=cpu_model(), n=cores(), avg=load(),
                              cpu_pct=await cpu_percent(), mem=memory(), top=top_memory(3))
        return "\n".join(lines)
