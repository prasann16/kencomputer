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


def _on_this_computer(name: str, version: str) -> str | None:
    seen = set()
    for folder in os.environ.get("PATH", "").split(os.pathsep) + PLACES:
        program = Path(folder).expanduser() / name
        if not folder or program in seen or not program.is_file() or not os.access(program, os.X_OK):
            continue
        seen.add(program)
        try:
            out = subprocess.run([str(program), "--version"], capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if _good_enough(name, _version(out), _version(version)):
            return str(program)
    return None


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
            while chunk := r.read(1 << 20):
                digest.update(chunk)
                f.write(chunk)
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
        if checked != pin["version"]:
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
        program = await asyncio.to_thread(_on_this_computer, name, pin["version"])
        if program:
            _folder().mkdir(parents=True, exist_ok=True)
            (_folder() / f"{name}.path").write_text(f"{program}\n{pin['version']}")
        else:
            label = {"claude": "Claude Code", "codex": "Codex"}[name]
            try:
                program = await asyncio.to_thread(_download, name, pin)
            except Exception as exc:
                raise RuntimeError(f"Couldn’t download {label}. Check the internet and try again. ({exc})") from exc
        _use(name, program)


def missing(name: str) -> bool:
    """`name` would have to be found or downloaded before it can run."""
    return bool(_pins().get(name)) and not _pip_has(name) and not (name in _chosen and Path(_chosen[name]).is_file()) \
        and not (name == "codex" and os.environ.get("CODEX_BIN"))


async def update() -> None:
    """After Ken updates to a newer Claude Code or Codex, get it in the background before it's needed,
    for the ones this computer already uses."""
    for name in _pins():
        used = any(_folder().glob(f"{name}-*")) or (_folder() / f"{name}.path").exists()
        if used and missing(name):
            try:
                await ensure(name)
            except Exception:
                pass  # tried again when it's needed
