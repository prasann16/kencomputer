"""First-run setup without a terminal: connect a brain, report voice readiness.

Connecting runs the brain's own sign-in CLI in a pseudo-terminal, the same one
the installer runs: `claude setup-token` (approve in the browser, paste the code
shown there, the long-lived token lands in ~/.ken/.env) or `codex login`
(approve in the browser and it finishes by itself).
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import re
import struct
import termios
import time
from pathlib import Path
from typing import Callable

from brains import Brain, Brains

log = logging.getLogger("ken")

ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[a-zA-Z~]|\x1b\][^\x07]*\x07|\x1b[=>]")
# Whisper download sizes in MB, for a progress bar while the voice model arrives.
WHISPER_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530, "large-v3": 3100}


def save_env(env_file: Path, key: str, value: str) -> None:
    """Set KEY=value in the .env file (and this process), keeping other lines."""
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
    env_file.write_text("\n".join(lines) + "\n")
    env_file.chmod(0o600)
    os.environ[key] = value


class Onboarding:
    def __init__(self, home: Path, brains: Brains, on_connected: Callable, voice_ready: Callable[[], bool]) -> None:
        self.env_file = home / ".env"
        self.brains = brains
        self.on_connected = on_connected
        self.voice_ready = voice_ready
        self.brain: Brain | None = None  # the one signing in
        self.settling: str | None = None  # signed in, but Ken is still fetching its models
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
        # A brain counts once its models are in, so the picker the app loads next has them.
        brains = [{"name": b.name, "label": b.label, "connected": b.name != self.settling and b.connected()}
                  for b in self.brains.all.values()]
        return {"connected": any(b["connected"] for b in brains), "brains": brains, "voice": self.voice_status()}

    def voice_status(self) -> dict:
        if self.voice_ready():
            return {"state": "ready"}
        name = os.environ.get("WHISPER_MODEL", "small")
        from huggingface_hub.constants import HF_HUB_CACHE

        cache = Path(HF_HUB_CACHE) / f"models--Systran--faster-whisper-{name}"
        got = sum(f.stat().st_size for f in cache.rglob("*") if f.is_file()) if cache.exists() else 0
        total = WHISPER_MB.get(name)
        return {"state": "downloading", "progress": min(0.99, got / (total * 1e6)) if total else None}

    # ---------------------------------------------------------------- sign-in

    async def start(self, name: str = "claude", device: bool = False) -> dict:
        """Start a brain's sign-in CLI; it opens the browser. Returns the sign-in URL
        as a fallback, the code to type there (device sign-in), and whether the user
        pastes a code back (Claude) or it finishes by itself (Codex)."""
        brain = self.brains.all.get(name)
        login = brain.login() if brain else None
        if login is None:
            raise RuntimeError(f"Ken can’t sign in to “{name}”.")
        self.stop()
        argv = login.device if device else login.argv
        pid, fd = pty.fork()
        if pid == 0:
            os.execv(argv[0], argv)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))  # wide: URL on one line
        os.set_blocking(fd, False)
        self.brain, self.pid, self.fd, self.output, self.exited = brain, pid, fd, "", False
        self.started = time.time()
        asyncio.get_running_loop().add_reader(fd, self._read)
        url = await self._wait_for(login.url, 30)
        code = await self._wait_for(login.code, 5) if login.code and device else ""
        if not url:
            self.stop()
            raise RuntimeError(f"Couldn’t open {brain.label} sign-in. Try again.")
        if not login.paste:
            asyncio.create_task(self._finish_by_itself(brain, pid))
        return {"brain": brain.name, "url": url, "code": code, "paste": login.paste}

    async def _finish_by_itself(self, brain: Brain, pid: int) -> None:
        """A sign-in that completes in the browser: the CLI exits once it's saved."""
        while self.pid == pid and not self.exited and self.active:
            await asyncio.sleep(1)
        if self.pid == pid:
            self.stop()
        brain.forget()
        if brain.connected():
            self.settling = brain.name
            try:
                await self.on_connected()
            except Exception:
                log.exception("post-connect refresh failed")
            finally:
                self.settling = None

    async def submit(self, code: str) -> dict:
        """Give the CLI the code from the browser; save the token it prints.

        If the sign-in has gone away (the CLI exited, or was never started),
        open a fresh one instead of failing: the user approves and pastes again."""
        brain = self.brain or self.brains.all["claude"]
        login = brain.login()
        try:
            if self.fd is None or self.exited:
                raise OSError("sign-in not running")
            os.write(self.fd, code.strip().encode() + b"\r")
        except OSError:
            return {**await self.start(brain.name), "error": "That sign-in had expired, so a new one just opened. Approve it, then paste the new code."}
        token = await self._wait_for(login.token, 60) if login.token else ""
        self.stop()
        if login.token and not token:
            raise RuntimeError("That code didn’t work. Connect again and paste the new code.")
        if token:
            save_env(self.env_file, login.env_key, token)
        brain.forget()
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
