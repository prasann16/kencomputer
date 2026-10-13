"""First-run setup without a terminal: connect a brain, report voice readiness.

Connecting runs the brain's own sign-in CLI in a pseudo-terminal, the same one
the installer runs, and the user approves in the browser. Usually that finishes
the sign-in by itself: `claude setup-token` prints a long-lived token (it lands
in ~/.ken/.env), `codex login` saves its own and exits. If Claude shows a code
instead, the user pastes it.
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

from brains import Brain, Brains, Login

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
        self.watch: asyncio.Task | None = None
        self.saved = ""

    @property
    def active(self) -> bool:
        """A sign-in is under way: the app must not restart the engine (e.g. to update) now."""
        return self.pid is not None and not self.exited and time.time() - self.started < 1800

    @property
    def pending(self) -> bool:
        """A sign-in hasn't finished: still waiting on the browser, or on Ken's refresh after it."""
        return bool(self.watch and not self.watch.done())

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
        self.saved = ""
        self.watch = asyncio.create_task(self._watch(brain, login))
        return {"brain": brain.name, "url": url, "code": code, "paste": login.paste}

    async def _watch(self, brain: Brain, login: Login) -> None:
        """Approving in the browser usually finishes the sign-in with no code to paste:
        save the token the CLI prints (Claude), or wait for it to save its own and exit (Codex)."""
        if login.token:
            token = await self._wait_for(login.token, 1800)
            if token:
                await self._save(brain, login, token)
        else:
            while not self.exited and self.active:
                await asyncio.sleep(1)
            await self._settle(brain)
        self.stop()

    async def _save(self, brain: Brain, login: Login, token: str) -> None:
        if token == self.saved:  # the browser and a pasted code can both deliver it
            return
        self.saved = token
        save_env(self.env_file, login.env_key, token)
        await self._settle(brain)

    async def _settle(self, brain: Brain) -> None:
        """Signed in: reopen sessions and fetch its models before it counts as connected."""
        brain.forget()
        if not brain.connected():
            return
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
        if self.saved:  # the browser already finished the sign-in
            return self.status()
        try:
            if self.fd is None or self.exited:
                raise OSError("sign-in not running")
            os.write(self.fd, code.strip().encode() + b"\r")
        except OSError:
            return {**await self.start(brain.name), "error": "That sign-in had expired, so a new one just opened. Approve it, then paste the new code."}
        token = await self._wait_for(login.token, 60)
        self.stop()
        if not token:
            raise RuntimeError("That code didn’t work. Connect again and paste the new code.")
        await self._save(brain, login, token)
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
        if self.watch and self.watch is not asyncio.current_task() and not self.settling:
            self.watch.cancel()  # a sign-in that already landed finishes its refresh
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
