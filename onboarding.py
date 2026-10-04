"""First-run setup without a terminal: connect Claude, report voice readiness.

Connecting runs the same `claude setup-token` the installer runs, in a
pseudo-terminal: the CLI opens the browser, the user approves and pastes the
code shown there, and the long-lived token lands in ~/.ken/.env.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import re
import shutil
import struct
import termios
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

        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
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


class Onboarding:
    def __init__(self, home: Path, connected: Callable[[], bool], on_connected: Callable, voice_ready: Callable[[], bool]) -> None:
        self.env_file = home / ".env"
        self.connected = connected
        self.on_connected = on_connected
        self.voice_ready = voice_ready
        self.pid: int | None = None
        self.fd: int | None = None
        self.output = ""
        self.exited = False
        self.started = 0.0

    @property
    def active(self) -> bool:
        """A sign-in is under way: the app must not restart the engine (e.g. to update) now."""
        return self.pid is not None and not self.exited and time.time() - self.started < 1800

    def status(self) -> dict:
        return {"claude": self.connected(), "voice": self.voice_status()}

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
        return {"url": url}

    async def submit(self, code: str) -> dict:
        """Give the CLI the code from the browser; save the token it prints.

        If the sign-in has gone away (the CLI exited, or was never started),
        open a fresh one instead of failing: the user approves and pastes again."""
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
        save_env(self.env_file, "CLAUDE_CODE_OAUTH_TOKEN", token)
        try:
            await self.on_connected()  # the token is saved either way
        except Exception:
            log.exception("post-connect refresh failed")
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
