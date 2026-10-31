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

CURSOR_RIGHT = re.compile(r"\x1b\[(\d*)C")  # how the CLI draws spaces
ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[a-zA-Z~]|\x1b\][^\x07]*\x07|\x1b[=>]")
AUTH_URL = re.compile(r"https://(?:claude\.com|claude\.ai|platform\.claude\.com)/\S*oauth\S*")
TOKEN = re.compile(r"sk-ant-oat[A-Za-z0-9_-]+")
# What the CLI prints when the sign-in goes wrong, e.g. "OAuth error: …" or "Failed to exchange authorization code…".
FAILED = re.compile(r"(?im)^.*\b(error|failed|denied|not allowed|not permitted|invalid|expired)\b.*$")
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
        self.error = ""

    @property
    def active(self) -> bool:
        """A sign-in is under way: the app must not restart the engine (e.g. to update) now."""
        return self.pid is not None and not self.exited and time.time() - self.started < 1800

    def status(self) -> dict:
        return {"claude": self.connected(), "connected": self.connected(), "backend": "claude",
                "phone": self.phone_step(), "voice": self.voice_status(), **({"error": self.error} if self.error else {})}

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
        self.error = ""
        # The CLI opens the browser through this, so Ken learns the link it opened: that one finishes
        # the sign-in by itself, unlike the code-to-paste link the CLI prints for other computers.
        opener = self.env_file.parent / "signin-browser"
        opener.write_text('#!/bin/sh\nprintf %s "$1" > "$0.url"\n'
                          'if command -v open >/dev/null; then exec open "$1"; else exec xdg-open "$1"; fi\n')
        opener.chmod(0o700)
        Path(str(opener) + ".url").unlink(missing_ok=True)
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["BROWSER"] = str(opener)
            os.execv(claude_cli(), [claude_cli(), "setup-token"])
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))  # wide: URL on one line
        os.set_blocking(fd, False)
        self.pid, self.fd, self.output, self.exited = pid, fd, "", False
        self.started = time.time()
        asyncio.get_running_loop().add_reader(fd, self._read)
        url = await self._wait_for(AUTH_URL, 30)
        if not url:
            self.stop()
            self._failed("Couldn’t open Claude sign-in. Try again.", "no sign-in link")
            raise RuntimeError(self.error)
        self.watch = asyncio.create_task(self._watch())
        try:
            opened = Path(str(opener) + ".url").read_text().strip()
        except OSError:
            opened = ""
        return {"url": url, **({"browser_url": opened} if opened else {})}

    async def _watch(self) -> None:
        """Approving in the browser usually finishes the sign-in with no code to paste: save the token the CLI prints.
        If Claude says no instead, or the CLI stops, say so on screen rather than waiting forever."""
        for _ in range(1800 * 5):
            if token := TOKEN.search(self.output):
                await self._save(token.group(0))
                break
            if problem := FAILED.search(self.output):
                said = re.sub(r"\s*Press Enter.*$", "", problem.group(0)).strip()
                self._failed("Claude said: " + said, said)
                break
            if self.exited:
                self._failed("The Claude sign-in stopped before it finished. Try again.", "cli exited")
                break
            await asyncio.sleep(0.2)
        else:
            self._failed("The Claude sign-in timed out. Try again.", "timed out")
        self.stop()

    def _failed(self, message: str, reason: str) -> None:
        """Show why on screen, and count it (no account details) so a broken sign-in shows up in Ken's reports."""
        if "subscription" in message.lower():
            message = "Ken needs a Claude Pro or Max plan. " + message
        self.error = message
        log.warning("Claude sign-in failed: %s", reason)
        import crash
        crash.track("connect_failed", tool="claude", reason=reason)

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
        self.error = ""
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
        text = CURSOR_RIGHT.sub(lambda m: " " * int(m.group(1) or 1), chunk.decode(errors="replace"))
        self.output = (self.output + ANSI.sub("", text))[-20000:]

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


class CodexOnboarding(Onboarding):
    """Use Codex's account API; tokens stay in Codex's own credential store."""

    def __init__(self, home, on_connected, voice_ready):
        self._connected = False
        super().__init__(home, lambda: self._connected, on_connected, voice_ready)
        self.client = self.login = None
        self.error = ""

    @property
    def active(self):
        return self.watch is not None and not self.watch.done()

    def status(self):
        return {"connected": self._connected, "backend": "codex", "phone": self.phone_step(),
                "voice": self.voice_status(), **({"error": self.error} if self.error else {})}

    async def refresh(self):
        from codex_brain import codex_client

        try:
            async with codex_client() as client:
                account = await client.account()
                self._connected = bool(os.environ.get("OPENAI_API_KEY") or account.account or not account.requires_openai_auth)
            self.error = ""
        except Exception as exc:
            self._connected = False
            self.error = f"Could not connect to Codex: {exc}"
            log.warning("Codex account check failed: %s", exc)

    async def start(self):
        from codex_brain import codex_client

        self.stop()
        await self.refresh()
        if self._connected:
            await self.on_connected()
            return self.status()
        self.client = codex_client()
        try:
            self.login = await self.client.login_chatgpt()
        except BaseException:
            await self.client.close()
            self.client = None
            raise
        self.error = ""
        self.watch = asyncio.create_task(self._watch_login(self.client, self.login))
        return {"url": self.login.auth_url, "backend": "codex"}

    async def _watch_login(self, client, login):
        try:
            result = await login.wait()
            if not result.success:
                raise RuntimeError(result.error or "Codex sign-in failed. Try again.")
            self._connected = True
            await self.on_connected()
        except asyncio.CancelledError:
            try:
                await login.cancel()
            except Exception:
                pass
            raise
        except Exception as exc:
            self.error = str(exc)
        finally:
            await client.close()

    async def submit(self, code):
        raise RuntimeError("Complete Codex sign-in in your browser; no code needs to be pasted here.")

    def stop(self):
        if self.watch and not self.watch.done():
            self.watch.cancel()
        self.watch = None


class BrainOnboarding:
    """Both accounts stay connected; each chat chooses its own runtime."""

    def __init__(self, providers, backend):
        self.providers, self.backend = providers, backend

    def provider(self, backend):
        try:
            return self.providers[self.backend() if backend is None else backend]
        except (KeyError, TypeError):
            raise ValueError("Choose Claude or Codex.") from None

    @property
    def active(self):
        return any(p.active for p in self.providers.values())

    def status(self):
        providers = {name: p.status() for name, p in self.providers.items()}
        return {"providers": providers, "backend": self.backend(),
                "connected": any(p["connected"] for p in providers.values()),
                "phone": self.phone_step(), "voice": self.providers["claude"].voice_status()}

    def phone_step(self):
        return self.providers["claude"].phone_step()

    def skip_phone(self):
        self.providers["claude"].skip_phone()
        return self.status()

    async def start(self, backend=None):
        result = await self.provider(backend).start()
        return {**result, **self.status()}

    async def submit(self, code, backend=None):
        await self.provider(backend).submit(code)
        return self.status()

    def stop(self):
        for provider in self.providers.values():
            provider.stop()
