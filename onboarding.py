"""First-run setup without a terminal: connect Claude, report voice readiness.

Connecting runs the same `claude setup-token` the installer runs, in a
pseudo-terminal: the CLI opens the browser and the user approves. Usually the
browser hands the sign-in straight back to the CLI; if Claude shows a code
instead, the user pastes it. Either way the long-lived token lands in ~/.ken/.env.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import struct
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("ken")

ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[a-zA-Z~]|\x1b\][^\x07]*\x07|\x1b[=>]")
AUTH_URL = re.compile(r"https://(?:claude\.com|claude\.ai|platform\.claude\.com)/\S*oauth\S*")
TOKEN = re.compile(r"sk-ant-oat[A-Za-z0-9_-]+")
# Whisper download sizes in MB, for a progress bar while the voice model arrives.
WHISPER_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530, "large-v3": 3100}


def claude_cli() -> str:
    """The Claude CLI that ships with the Agent SDK, else one on PATH."""
    try:
        import claude_agent_sdk

        for name in ("claude", "claude.exe"):
            bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
            if bundled.exists():
                return str(bundled)
    except ImportError:
        pass
    return shutil.which("claude") or "claude"


def save_env(env_file: Path, key: str, value: str) -> None:
    """Set KEY=value in the .env file (and this process), keeping other lines."""
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
    env_file.write_text("\n".join(lines) + "\n")
    env_file.chmod(0o600)
    os.environ[key] = value


def drop_env(env_file: Path, key: str) -> None:
    """Remove KEY from the .env file (and this process)."""
    if env_file.exists():
        env_file.write_text("".join(l + "\n" for l in env_file.read_text().splitlines() if not l.startswith(key + "=")))
    os.environ.pop(key, None)


class Onboarding:
    def __init__(self, home: Path, connected: Callable[[], bool], on_connected: Callable, voice_ready: Callable[[], bool]) -> None:
        self.env_file = home / ".env"
        self.setup_file = home / "setup.json"
        self.connected = connected
        self.on_connected = on_connected
        self.voice_ready = voice_ready
        self.pid: int | None = None
        self.fd: int | None = None
        self.output = ""
        self.exited = False
        self.started = 0.0
        self.watch: asyncio.Task | None = None
        self.saved = ""

    @property
    def active(self) -> bool:
        """A sign-in is under way: the app must not restart the engine (e.g. to update) now."""
        return self.pid is not None and not self.exited and time.time() - self.started < 1800

    def status(self) -> dict:
        return {"claude": self.connected(), "phone": self.phone_step(), "voice": self.voice_status()}

    # The second fixed step: Ken on your phone, which they can skip. "unset" until done or skipped.
    def _setup(self) -> dict:
        try:
            data = json.loads(self.setup_file.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def phone_step(self) -> str:
        if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("ALLOWED_USER_ID"):
            return "done"  # paired, perhaps before this step existed
        return self._setup().get("phone", "unset")

    def skip_phone(self) -> dict:
        data = self._setup()
        data["phone"] = "declined"  # Settings, Phone still works any time
        self.setup_file.write_text(json.dumps(data, indent=1) + "\n")
        return self.status()

    def voice_status(self) -> dict:
        if self.voice_ready():
            return {"state": "ready"}
        name = os.environ.get("WHISPER_MODEL", "small")
        from huggingface_hub.constants import HF_HUB_CACHE

        cache = Path(HF_HUB_CACHE) / f"models--Systran--faster-whisper-{name}"
        got = sum(f.stat().st_size for f in cache.rglob("*") if f.is_file()) if cache.exists() else 0
        total = WHISPER_MB.get(name)
        return {"state": "downloading", "progress": min(0.99, got / (total * 1e6)) if total else None}

    # ------------------------------------------------------------------ Claude

    async def start(self) -> dict:
        """Start `claude setup-token`; it opens the browser. Returns the sign-in URL as a fallback."""
        import fcntl  # Mac and Linux only, like the app that uses this
        import pty
        import termios

        self.stop()
        pid, fd = pty.fork()
        if pid == 0:
            os.execv(claude_cli(), [claude_cli(), "setup-token"])
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))  # wide: URL on one line
        os.set_blocking(fd, False)
        self.pid, self.fd, self.output, self.exited = pid, fd, "", False
        self.started = time.time()
        asyncio.get_running_loop().add_reader(fd, self._read)
        url = await self._wait_for(AUTH_URL, 30)
        if not url:
            self.stop()
            raise RuntimeError("Couldn’t open Claude sign-in. Try again.")
        self.watch = asyncio.create_task(self._watch())
        return {"url": url}

    async def _watch(self) -> None:
        """Approving in the browser usually finishes the sign-in with no code to paste: save the token the CLI prints."""
        token = await self._wait_for(TOKEN, 1800)
        if token:
            await self._save(token)
        self.stop()

    async def _save(self, token: str) -> None:
        if token == self.saved:  # the browser and a pasted code can both deliver it
            return
        self.saved = token
        save_env(self.env_file, "CLAUDE_CODE_OAUTH_TOKEN", token)
        try:
            await self.on_connected()  # the token is saved either way
        except Exception:
            log.exception("post-connect refresh failed")

    async def submit(self, code: str) -> dict:
        """Give the CLI the code from the browser; save the token it prints.

        If the sign-in has gone away (the CLI exited, or was never started),
        open a fresh one instead of failing: the user approves and pastes again."""
        if self.connected():  # the browser already finished the sign-in
            return self.status()
        try:
            if self.fd is None or self.exited:
                raise OSError("sign-in not running")
            os.write(self.fd, code.strip().encode() + b"\r")
        except OSError:
            return {**await self.start(), "error": "That sign-in had expired, so a new one just opened. Approve it, then paste the new code."}
        token = await self._wait_for(TOKEN, 60)
        self.stop()
        if not token:
            raise RuntimeError("That code didn’t work. Connect again and paste the new code.")
        await self._save(token)
        return self.status()

    def _read(self) -> None:
        try:
            chunk = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if not chunk:  # the CLI exited
            asyncio.get_running_loop().remove_reader(self.fd)
            self.exited = True
            return
        self.output = (self.output + ANSI.sub("", chunk.decode(errors="replace")))[-20000:]

    async def _wait_for(self, pattern: re.Pattern, seconds: float) -> str:
        for _ in range(int(seconds * 5)):
            if match := pattern.search(self.output):
                return match.group(0)
            if self.exited:
                break
            await asyncio.sleep(0.2)
        return ""

    def stop(self) -> None:
        if self.watch and self.watch is not asyncio.current_task():
            self.watch.cancel()
        self.watch = None
        if self.pid:
            try:
                os.kill(self.pid, 9)
                os.waitpid(self.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        if self.fd is not None:
            try:
                asyncio.get_running_loop().remove_reader(self.fd)
            except RuntimeError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.pid = self.fd = None
