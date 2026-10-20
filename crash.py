"""Crash reports: every error Ken hits goes to PostHog, so it can be read daily and fixed.

No SDK, just one HTTPS POST per error, sent off the main thread. What's sent: the error type,
its message and the stack trace (file, function, line), Ken's version and the OS. Never chats,
files, or keys: home paths become ~ and anything shaped like a token is masked.
Off with KEN_CRASH_REPORTS=0 in ~/.ken/.env.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform
import re
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

import httpx

# Ken's PostHog project key. It can only write events, never read them, so it's safe to ship.
PROJECT_KEY = ""
HOST = os.environ.get("KEN_POSTHOG_HOST", "https://us.i.posthog.com")
PER_HOUR = 30            # at most this many reports an hour; a loop that fails every second sends 30, not 3600
SAME_AGAIN_AFTER = 3600  # the same error twice in an hour is sent once

_HOME = str(Path.home())
_SECRETS = re.compile(
    r"\d{6,}:[A-Za-z0-9_-]{30,}"                 # Telegram bot tokens
    r"|sk-[A-Za-z0-9_-]{16,}"                    # Anthropic / OpenAI style keys
    r"|(?:phc|phx|ghp|gho|xox[bp])_[A-Za-z0-9_-]{16,}"
    r"|[A-Za-z0-9_-]{40,}"                       # anything else long and random
)

_state = {"id": "", "version": "", "sent": [], "seen": {}}
_lock = threading.Lock()


def key() -> str:
    return os.environ.get("KEN_POSTHOG_KEY", PROJECT_KEY)


def enabled() -> bool:
    return bool(key()) and os.environ.get("KEN_CRASH_REPORTS", "1") != "0"


def scrub(text: str) -> str:
    return _SECRETS.sub("[hidden]", str(text).replace(_HOME, "~"))


def install_id(home: Path) -> str:
    """A random id per install, so reports group by machine. Not tied to the person."""
    f = home / "install-id"
    try:
        if not f.exists():
            f.write_text(uuid.uuid4().hex)
        return f.read_text().strip()
    except OSError:
        return "unknown"


def _frames(tb) -> list[dict]:
    frames = []
    for fs in traceback.extract_tb(tb):
        path = scrub(fs.filename)
        frames.append({
            "platform": "python", "filename": path.rsplit("/", 1)[-1], "abs_path": path,
            "function": fs.name, "lineno": fs.lineno, "context_line": scrub(fs.line or ""),
            "in_app": "site-packages" not in path and "_internal" not in path,
        })
    return frames


def event(exc: BaseException | None, message: str = "", handled: bool = True, where: str = "") -> dict:
    """The PostHog $exception event for an error (or a logged error with no exception)."""
    if exc is not None:
        kind, value, frames = type(exc).__name__, scrub(exc), _frames(exc.__traceback__)
    else:
        kind, value, frames = "LoggedError", "", []
    note = scrub(message)
    fingerprint = hashlib.sha1(
        f"{kind}|{where}|" .encode() + "|".join(f"{f['filename']}:{f['function']}" for f in frames[-5:]).encode()
        + (b"" if frames else note[:120].encode())
    ).hexdigest()
    return {
        "event": "$exception",
        "distinct_id": _state["id"] or "unknown",
        "properties": {
            "$exception_list": [{
                "type": kind, "value": value or note,
                "mechanism": {"type": "generic", "handled": handled},
                "stacktrace": {"type": "raw", "frames": frames},
            }],
            "$exception_fingerprint": fingerprint,
            "log_message": note, "where": where,
            "ken_version": _state["version"], "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(), "python": platform.python_version(),
            "$process_person_profile": False,
        },
    }


def _allowed(fingerprint: str) -> bool:
    now = time.time()
    with _lock:
        _state["sent"] = [t for t in _state["sent"] if now - t < 3600]
        if len(_state["sent"]) >= PER_HOUR or now - _state["seen"].get(fingerprint, 0) < SAME_AGAIN_AFTER:
            return False
        _state["sent"].append(now)
        _state["seen"][fingerprint] = now
        return True


def _post(payload: dict) -> None:
    try:
        httpx.post(f"{HOST}/i/v0/e/", json={"api_key": key(), **payload}, timeout=5)
    except Exception:
        pass  # a crash report must never cause a crash


def report(exc: BaseException | None, message: str = "", handled: bool = True, where: str = "") -> None:
    if not enabled():
        return
    try:
        payload = event(exc, message, handled, where)
    except Exception:
        return
    if _allowed(payload["properties"]["$exception_fingerprint"]):
        threading.Thread(target=_post, args=(payload,), daemon=True, name="ken-crash-report").start()


class _Handler(logging.Handler):
    """log.error / log.exception anywhere in Ken becomes a report."""

    def emit(self, record: logging.LogRecord) -> None:
        exc = record.exc_info[1] if record.exc_info else None
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        report(exc, msg, handled=True, where=f"{record.module}.{record.funcName}")


def loop_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Errors in background tasks nobody awaited: report, then let asyncio log them as usual."""
    report(context.get("exception"), context.get("message", ""), handled=False, where="asyncio")
    loop.default_exception_handler(context)


def setup(home: Path, version: str) -> None:
    """Catch everything: logged errors, uncaught exceptions, thread crashes. Call once at start."""
    _state["id"], _state["version"] = install_id(home), version
    handler = _Handler(level=logging.ERROR)
    logging.getLogger().addHandler(handler)

    previous = sys.excepthook

    def excepthook(kind, exc, tb):
        if not issubclass(kind, KeyboardInterrupt):
            report(exc, handled=False, where="uncaught")
        previous(kind, exc, tb)

    sys.excepthook = excepthook
    prev_thread = threading.excepthook

    def thread_hook(args):
        report(args.exc_value, handled=False, where=f"thread {getattr(args.thread, 'name', '')}")
        prev_thread(args)

    threading.excepthook = thread_hook


def flush(seconds: float = 3) -> None:
    """Before exiting on a crash: give the last report a moment to leave."""
    end = time.time() + seconds
    for t in threading.enumerate():
        if t.name == "ken-crash-report":
            t.join(max(0, end - time.time()))


# Usage: what people do with Ken, counted, never read. Event names and small fixed values only:
# no message text, file names, emails, calendar contents or paths. Off with KEN_USAGE_STATS=0.
EVENTS_PER_HOUR = 300
_events: list[float] = []


def usage_enabled() -> bool:
    return bool(key()) and os.environ.get("KEN_USAGE_STATS", "1") != "0"


def track(name: str, **props) -> None:
    """Count one thing that happened, e.g. track("message", via="telegram", outcome="ok")."""
    if not usage_enabled():
        return
    now = time.time()
    with _lock:
        _events[:] = [t for t in _events if now - t < 3600]
        if len(_events) >= EVENTS_PER_HOUR:
            return
        _events.append(now)
    safe = {k: (v if isinstance(v, (bool, int, float)) or v is None else scrub(v)[:80]) for k, v in props.items()}
    payload = {
        "event": name,
        "distinct_id": _state["id"] or "unknown",
        "properties": {
            **safe, "ken_version": _state["version"], "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(), "$process_person_profile": False,
        },
    }
    threading.Thread(target=_post, args=(payload,), daemon=True, name="ken-crash-report").start()
