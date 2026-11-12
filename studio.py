"""HyperFrames Studio for a reel Ken made: the timeline editor, run on this computer, so the person can change anything
by hand (text, timing, clips) from a link, in any browser.

Ken builds reels as HyperFrames projects (a folder under videos/ with hyperframes.json). `ensure(project)` starts
Studio for that project on a private port here (127.0.0.1 only); the web app passes /studio/* through to it, and the
host (ken-hosted) puts it on its own address behind the person's sign-in. One project at a time: opening another stops
the first. Studio saves into the project, so Ken sees every change and renders the final video from it.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

PORT = int(os.environ.get("KEN_STUDIO_PORT") or 4590)
VERSION = "0.9.25"  # pinned: the editor Ken was tested with
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")

_proc: asyncio.subprocess.Process | None = None
_project: Path | None = None
_lock = asyncio.Lock()


def project_dir(videos: Path, name: str) -> Path | None:
    """The HyperFrames project for a reel: videos/<name>/edit (what the video skill makes), or videos/<name> itself.
    Nothing outside videos/ can be named."""
    if not NAME.match(name or ""):
        return None
    reel = (videos / name).resolve()
    if reel.parent != videos.resolve():
        return None
    for d in (reel / "edit", reel):
        if (d / "hyperframes.json").is_file():
            return d
    return None


async def _answers(timeout: float) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", PORT), 2)
            w.close()
            return True
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.5)
    return False


async def ensure(project: Path) -> None:
    """Studio running for this project (started, or switched to it). Raises with plain words if it can't start."""
    global _proc, _project
    async with _lock:
        if _proc and _proc.returncode is None and _project == project and await _answers(1):
            return
        await stop()
        npx = shutil.which("npx")
        if not npx:
            raise RuntimeError("The video editor needs Node, which isn't on this computer.")
        env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "HYPERFRAMES_SKIP_SKILLS": "1", "BROWSER": "none"}
        _proc = await asyncio.create_subprocess_exec(
            npx, "-y", f"hyperframes@{VERSION}", "preview", "--foreground", "--port", str(PORT),
            cwd=str(project), env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        _project = project
        if not await _answers(120):  # the first time, npx fetches HyperFrames (a minute or so)
            await stop()
            raise RuntimeError("The video editor didn't start. Try again in a minute.")


async def stop() -> None:
    global _proc, _project
    if _proc and _proc.returncode is None:
        _proc.terminate()
        try:
            await asyncio.wait_for(_proc.wait(), 10)
        except asyncio.TimeoutError:
            _proc.kill()
    _proc, _project = None, None


def link(name: str) -> str:
    """The edit link for a project: on the host's site, behind the person's sign-in."""
    url = os.environ.get("KEN_KEEPALIVE_URL", "")
    site = url.rsplit("/box/", 1)[0] if url.startswith("https://") and "/box/" in url else ""
    site = os.environ.get("KEN_STUDIO_SITE", "").rstrip("/") or site  # a stand-in for the host, when testing
    return f"{site}/studio/{name}" if site else ""
