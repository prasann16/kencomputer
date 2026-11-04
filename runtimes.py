"""Where Claude Code and Codex come from.

Terminal installs get both from pip. The Mac app leaves them out (they'd make it ~550 MB bigger, and
most people use only one): it uses the copy already on this computer when that's new enough, or
downloads the exact one Ken was built with, once, the first time it's needed.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# Written by desktop/build_engine.py: for each runtime, the pip wheel Ken was built with.
PINS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "runtimes.json"
# Inside each wheel: the program, and for Codex the helper folder it wants on PATH.
INSIDE = {"claude": ("claude_agent_sdk/_bundled/claude", None),
          "codex": ("codex_cli_bin/bin/codex", "codex_cli_bin/codex-path")}
# Where a copy on this computer may live (the app's own PATH is short).
PLACES = ["/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/.claude/local", "~/.npm-global/bin",
          "~/.bun/bin", "/Applications/Codex.app/Contents/Resources"]

_chosen: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}
_state: dict[str, dict] = {}  # while getting one: {"progress": 0.4}; after it failed: {"error": "…"}


def _pins() -> dict:
    try:
        return json.loads(PINS.read_text())
    except (OSError, ValueError):
        return {}


def _folder() -> Path:
    return Path(os.environ.get("KEN_HOME", str(Path.home() / ".ken"))) / "runtimes"


def _pip_has(name: str) -> bool:
    """The pip install (terminal Ken) ships the runtime: nothing to do."""
    module = {"claude": "claude_agent_sdk", "codex": "codex_cli_bin"}[name]
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        return False
    if spec is None or not spec.origin:
        return False
    return (Path(spec.origin).parent.parent / INSIDE[name][0]).is_file()


def _use(name: str, program: str) -> None:
    _chosen[name] = program
    folders = [str(Path(program).parent)]
    helper = INSIDE[name][1]
    if helper and (Path(program).parents[2] / helper).is_dir():  # a downloaded Codex's ripgrep
        folders.append(str(Path(program).parents[2] / helper))
    path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p not in folders]
    os.environ["PATH"] = os.pathsep.join(folders + path)  # the Claude SDK finds `claude` on PATH


def _version(text: str) -> tuple:
    for word in text.split():
        parts = word.strip("v").split(".")
        if len(parts) >= 2 and all(p.isdigit() for p in parts):
            return tuple(int(p) for p in parts)
    return ()


def _good_enough(name: str, found: tuple, pinned: tuple) -> bool:
    # Claude Code stays compatible going forward; Codex's app-server changes with each minor version.
    return found >= pinned if name == "claude" else found[:2] == pinned[:2]


def _runs(pin: dict) -> str:
    """The program's own version (Claude Code 2.1.300), not its pip package's (claude-agent-sdk 0.2.167)."""
    return pin.get("runs") or pin["version"]


def _on_this_computer(name: str, version: str) -> str | None:
    """The newest copy on this computer, if it's new enough (a Mac can have an old one lying around)."""
    seen, best = set(), (None, ())
    for folder in os.environ.get("PATH", "").split(os.pathsep) + PLACES:
        program = Path(folder).expanduser() / name
        if not folder or program in seen or not program.is_file() or not os.access(program, os.X_OK):
            continue
        seen.add(program)
        try:
            out = subprocess.run([str(program), "--version"], capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        found = _version(out)
        if _good_enough(name, found, _version(version)) and found > best[1]:
            best = (str(program), found)
    return best[0]


def _download(name: str, pin: dict) -> str:
    folder = _folder() / f"{name}-{pin['version']}"
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder.parent) as tmp:
        wheel, digest = Path(tmp) / "wheel.zip", hashlib.sha256()
        try:  # the app's own Python may not find the Mac's certificates
            import certifi, ssl
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = None
        with urllib.request.urlopen(pin["url"], timeout=60, context=context) as r, open(wheel, "wb") as f:
            size, got = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 20):
                digest.update(chunk)
                f.write(chunk)
                got += len(chunk)
                _state[name] = {"progress": round(min(0.99, got / size), 2) if size else None}
        if digest.hexdigest() != pin["sha256"]:
            raise RuntimeError("The download was damaged. Try again.")
        out = Path(tmp) / "out"
        keep = Path(INSIDE[name][0]).parts[0] + "/"  # the whole package: Codex has helpers next to it
        with zipfile.ZipFile(wheel) as z:
            names = [n for n in z.namelist() if n.startswith(keep)]
            z.extractall(out, names)
        for n in names:  # zip doesn't keep the "can run" flag
            if not n.endswith("/"):
                (out / n).chmod(0o755)
        shutil.rmtree(folder, ignore_errors=True)
        out.rename(folder)
    for old in folder.parent.glob(f"{name}-*"):  # the one an older Ken used
        if old != folder:
            shutil.rmtree(old, ignore_errors=True)
    return str(folder / INSIDE[name][0])


def setup() -> None:
    """At start: use what an earlier start chose or downloaded (no slow checks)."""
    pins = _pins()
    for name, pin in pins.items():
        if _pip_has(name):
            continue
        downloaded = _folder() / f"{name}-{pin['version']}" / INSIDE[name][0]
        try:  # a copy on this computer, checked against the version this Ken was built with
            remembered, checked = ((_folder() / f"{name}.path").read_text().split("\n") + [""])[:2]
        except OSError:
            remembered, checked = "", ""
        if checked != _runs(pin):
            remembered = ""
        for program in (remembered, str(downloaded)):
            if program and Path(program).is_file():
                _use(name, program)
                break


def codex_bin() -> str | None:
    """The Codex for CodexConfig.codex_bin, or None for pip's own."""
    return os.environ.get("CODEX_BIN") or _chosen.get("codex")


async def ensure(name: str) -> None:
    """Make sure `name` can run: a good copy on this computer, else a one-time download. Raises if it can't."""
    if not missing(name):
        return
    pin = _pins()[name]
    async with _locks.setdefault(name, asyncio.Lock()):
        if not missing(name):
            return
        _state[name] = {"progress": 0}
        try:
            program = await asyncio.to_thread(_on_this_computer, name, _runs(pin))
            if program:
                _folder().mkdir(parents=True, exist_ok=True)
                (_folder() / f"{name}.path").write_text(f"{program}\n{_runs(pin)}")
            else:
                program = await asyncio.to_thread(_download, name, pin)
        except Exception as exc:
            label = {"claude": "Claude", "codex": "Codex"}[name]
            _state[name] = {"error": f"Couldn’t download {label}. Check the internet and try again."}
            logging.getLogger(__name__).warning("getting %s failed: %s", name, exc)
            raise RuntimeError(_state[name]["error"]) from exc
        _state.pop(name, None)
        _use(name, program)


def status() -> dict:
    """The ones being downloaded ({"progress"}) or that failed ({"error"}), for the app."""
    return {name: dict(s) for name, s in _state.items()}


def get_ready(name: str) -> None:
    """Start getting `name` in the background, for the app's Try again and for a start that needs it."""
    if missing(name) and "progress" not in _state.get(name, {}):
        task = asyncio.get_running_loop().create_task(ensure(name))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())  # the failure is in status()
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


_tasks: set = set()


def missing(name: str) -> bool:
    """`name` would have to be found or downloaded before it can run."""
    return bool(_pins().get(name)) and not _pip_has(name) and not (name in _chosen and Path(_chosen[name]).is_file()) \
        and not (name == "codex" and os.environ.get("CODEX_BIN"))


async def update(signed_in=()) -> None:
    """At start, get what's needed in the background before the first message: the ones this computer
    has used (after a Ken update to newer ones) and the accounts signed in (after the update that took
    them out of the app)."""
    for name in _pins():
        used = any(_folder().glob(f"{name}-*")) or (_folder() / f"{name}.path").exists()
        if (used or name in signed_in) and missing(name):
            try:
                await ensure(name)
            except Exception:
                pass  # tried again when it's needed
