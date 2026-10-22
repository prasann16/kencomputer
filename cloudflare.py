"""Local Cloudflare setup checks. Never change an existing tunnel or expose its token."""
from __future__ import annotations

import asyncio
import os
import platform
import plistlib
import re
import shutil
import signal
from pathlib import Path

import yaml
from yarl import URL


def executable(name):
    candidates = [shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}",
                  str(Path.home() / ".local/bin" / name)]
    return next((p for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)), None)


def small_file(path):
    with path.open("rb") as f:
        data = f.read(256 * 1024 + 1)
    if len(data) > 256 * 1024:
        raise ValueError("File too large")
    return data


def scan(port, *, home=None, config_dirs=None, service_dirs=None):
    """Only known config/service locations. Return routes, never credentials or raw files."""
    home = home or Path.home()
    config_dirs = config_dirs if config_dirs is not None else [home / ".cloudflared", Path("/etc/cloudflared"),
                                                              Path("/usr/local/etc/cloudflared"), Path("/opt/homebrew/etc/cloudflared")]
    service_dirs = service_dirs if service_dirs is not None else [home / "Library/LaunchAgents", Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons")]
    configs = {d / name for d in config_dirs for name in ("config.yml", "config.yaml")}
    installed_service = False
    binary = executable("cloudflared")
    unreadable = False
    for directory in service_dirs:
        for path in list(directory.glob("*cloudflared*.plist"))[:20]:
            installed_service = True
            try:
                data = plistlib.loads(small_file(path))
                args = data.get("ProgramArguments", [])
                if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                    continue
                program = data.get("Program") or (args[0] if args else "")
                if isinstance(program, str) and Path(program).name == "cloudflared" and os.access(program, os.X_OK):
                    binary = binary or program
                for i, arg in enumerate(args):
                    config = args[i + 1] if arg == "--config" and i + 1 < len(args) else arg.removeprefix("--config=") if arg.startswith("--config=") else None
                    if config:
                        p = Path(config).expanduser()
                        if p.is_absolute():
                            configs.add(p)
            except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
                unreadable = True
    routes = set()
    found_config = False
    for path in sorted(configs):
        try:
            content = small_file(path)
            found_config = True
            config = yaml.safe_load(content)
            if not isinstance(config, dict):
                raise ValueError("Invalid config")
            ingress = config.get("ingress", [])
            if not isinstance(ingress, list):
                raise ValueError("Invalid ingress")
            for rule in ingress[:1000]:
                if not isinstance(rule, dict) or rule.get("path"):
                    continue
                origin = URL(str(rule.get("service", "")))
                hostname = rule.get("hostname", "")
                if (origin.scheme == "http" and origin.host in {"localhost", "127.0.0.1", "::1"}
                        and origin.port == port and isinstance(hostname, str)
                        and re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", hostname)
                        and "." in hostname and ".." not in hostname):
                    routes.add("https://" + hostname.lower())
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, RecursionError, yaml.YAMLError):
            unreadable = True
    return {"binary": binary, "brew": executable("brew"), "service_installed": installed_service,
            "config_found": found_config, "config_unreadable": unreadable, "routes": sorted(routes)}


async def command(*args, timeout=5, env=None, capture=True):
    """Fixed argv only; no shell, no prompts, and no orphaned install process on shutdown."""
    process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=True)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        return process.returncode, (output or b"").decode(errors="replace")
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


class CloudflareSetup:
    def __init__(self):
        self.install_task = None
        self.install_state = {"state": "idle"}

    async def status(self, port):
        state = await asyncio.to_thread(scan, port)

        async def version():
            if state["binary"]:
                try:
                    code, output = await command(state["binary"], "--version")
                    match = re.search(r"cloudflared version ([\w.+-]+)", output)
                    return match[1] if code == 0 and match else None
                except (OSError, asyncio.TimeoutError):
                    pass

        async def running():
            try:
                # comm excludes arguments: tunnel tokens never enter our output or API.
                code, output = await command("/bin/ps", "-axo", "comm=")
                if code == 0:
                    return any(Path(line.strip()).name == "cloudflared" for line in output.splitlines())
            except (OSError, asyncio.TimeoutError):
                pass
            return None

        ver, active = await asyncio.gather(version(), running())
        return {**state, "installed": bool(state["binary"]), "version": ver, "running": active,
                "platform": platform.system(), "arch": platform.machine(), "install": dict(self.install_state)}

    def install(self):
        if self.install_task and not self.install_task.done():
            return dict(self.install_state)
        if platform.system() != "Darwin":
            raise ValueError("Use Cloudflare’s installation guide for this operating system.")
        if executable("cloudflared"):
            return {"state": "done"}
        brew = executable("brew")
        if not brew:
            raise ValueError("Install Homebrew first, then choose Check again.")
        self.install_state = {"state": "running"}
        self.install_task = asyncio.create_task(self._install(brew))
        return dict(self.install_state)

    async def _install(self, brew):
        try:
            env = {**os.environ, "NONINTERACTIVE": "1", "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1"}
            code, _ = await command(brew, "install", "cloudflared", timeout=600, env=env, capture=False)
            if code != 0 or not executable("cloudflared"):
                raise ValueError
            self.install_state = {"state": "done"}
        except (OSError, ValueError, asyncio.TimeoutError):
            self.install_state = {"state": "error", "error": "Installation didn’t finish. Run brew install cloudflared in Terminal to see what needs attention, then check again."}
        except asyncio.CancelledError:
            self.install_state = {"state": "error", "error": "Installation was interrupted. Check again before retrying."}
            raise

    async def close(self):
        if self.install_task and not self.install_task.done():
            self.install_task.cancel()
            await asyncio.gather(self.install_task, return_exceptions=True)
