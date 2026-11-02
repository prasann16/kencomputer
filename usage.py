"""Usage: how much of the Claude plan is used, for /usage and a heads-up before it runs out.

Claude Code reports the plan's usage windows (5 hour, weekly) as rate limit events, only when they
change. Ken keeps the latest reading of each window in usage.json. When a window gets near its limit,
Ken says so once, with when it resets, so running out never comes as a surprise.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("ken")

WINDOW_NAMES = {"five_hour": "5 hour", "seven_day": "weekly", "seven_day_opus": "weekly Opus",
                "seven_day_sonnet": "weekly Sonnet", "overage": "extra usage"}
WARN_AT = 80  # % of a window used when Ken gives the heads-up


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


def near_limit(w: dict, now: float) -> bool:
    """Close enough to the limit to say so; a window past its reset no longer counts."""
    if w.get("resets_at") and w["resets_at"] <= now:
        return False
    return w.get("status") in ("allowed_warning", "rejected") or (w.get("pct") or 0) >= WARN_AT


def when(ts: float | None, now: float | None = None) -> str:
    """A reset time as they'd say it: '22:09' today, 'Fri 19:50' later."""
    if not ts:
        return "unknown"
    now = time.time() if now is None else now
    t = time.localtime(ts)
    return time.strftime("%H:%M" if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d", time.localtime(now))
                         else "%a %H:%M", t)


def bar(pct: float, width: int = 10) -> str:
    """A text meter for Telegram, which has no real progress bars: ▰▰▰▱▱▱▱▱▱▱ 30%."""
    filled = max(0, min(width, round(pct / 100 * width)))
    return "▰" * filled + "▱" * (width - filled) + f" {pct:.0f}%"


class Usage:
    """The latest plan readings and which heads-ups Ken has already given, in usage.json."""

    def __init__(self, home: Path, say: Callable[[str], None] = lambda text: None) -> None:
        self.path = Path(home) / "usage.json"
        self.say = say  # one message to them, in the app and on the phone

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
        """A rate limit event from any Claude session: keep each window's newest reading, and give
        the heads-up once per window when it's near its limit."""
        now = time.time() if now is None else now
        try:
            state = self.load()
            windows, said = state.setdefault("windows", {}), state.setdefault("said", {})
            for name, w in readings_from(raw, now).items():
                old = windows.get(name, {})
                if w.get("status") is None and old.get("resets_at") == w.get("resets_at"):
                    w["status"] = old.get("status")  # the status only comes with the window it's about
                windows[name] = w = {k: v for k, v in w.items() if v is not None}
                if near_limit(w, now) and said.get(name) != w.get("resets_at"):
                    said[name] = w.get("resets_at")
                    used = f"{w['pct']:.0f}%" if w.get("pct") is not None else "nearly all"
                    window = WINDOW_NAMES.get(name, name.replace("_", " "))
                    self.say(f"Heads-up: you've used {used} of your Claude plan's {window} limit. "
                             f"It resets {when(w.get('resets_at'), now)}.")
            self.save(state)
        except Exception as e:
            log.warning("couldn't save the usage reading: %s", e)

    def report(self, backend: str, now: float | None = None) -> str:
        """/usage: each window's meter and when it resets."""
        if backend != "claude":
            return "Usage is only shown for Claude. ChatGPT doesn't report it."
        now = time.time() if now is None else now
        windows = self.load().get("windows", {})
        if not windows:
            return "No usage reading yet. Claude reports it as you chat, so ask again after a reply or two."
        lines = ["Your Claude plan:"]
        for name in sorted(windows, key=lambda k: list(WINDOW_NAMES).index(k) if k in WINDOW_NAMES else 99):
            w = windows[name]
            label = WINDOW_NAMES.get(name, name.replace("_", " ")).capitalize()
            if w.get("resets_at") and w["resets_at"] <= now:
                lines.append(f"{label}: reset at {when(w['resets_at'], now)}")
                continue
            used = bar(w["pct"]) if w.get("pct") is not None else (w.get("status") or "no %").replace("_", " ")
            lines.append(f"{label}  {used} · resets {when(w.get('resets_at'), now)}")
        return "\n".join(lines)
