#!/usr/bin/env python3
"""Draw a visual explainer: one HTML file in, a phone sized PNG out, and for step by step visuals a short MP4.
Only needs Python 3 (standard library), a Chrome or Chromium on the computer, and ffmpeg for the MP4.

    python3 render.py new <folder> [--3d]        start a visual: copies the template to <folder>/visual.html
    python3 render.py <folder or visual.html>    visual.png (1080x1350)
        [--steps]                                also step-1.png ... step-N.png and steps.mp4 (from data-step="N")
        [--hold 2.6]                             seconds each step stays on screen in the MP4
        [--size 1080x1350]                       another size (1080x1920 for a Story, 1600x900 for a laptop)
        [--wait 4000]                            ms the page gets to load fonts and draw (raise it for heavy 3D)

The HTML is drawn in a throwaway Chrome profile, so it never touches anyone's own browser.
"""
from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent


def find_chrome() -> str:
    """A Chrome to draw with: $KEN_CHROME, one on the PATH, the usual app locations, then the Chrome the
    video tools download (Remotion's headless shell), in that order."""
    if os.environ.get("KEN_CHROME"):
        return os.environ["KEN_CHROME"]
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome",
                 "microsoft-edge", "msedge"):
        if shutil.which(name):
            return shutil.which(name)
    places = {
        "Darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Chromium.app/Contents/MacOS/Chromium",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        "Windows": [os.path.expandvars(p) for p in (
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe")],
    }.get(platform.system(), [])
    home = Path(os.environ.get("KEN_HOME", Path.home() / ".ken"))
    places += glob.glob(str(home / "tools" / "**" / ".remotion" / "**" / "chrome-headless-shell*"), recursive=True)
    for p in places:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise SystemExit("No Chrome or Chromium found. Install Chrome, or set KEN_CHROME to its path.")


def shoot(chrome: str, html: Path, out: Path, size: str, wait: int, step: int | None = None) -> None:
    w, h = size.split("x")
    url = html.resolve().as_uri() + (f"#step={step}" if step else "")
    with tempfile.TemporaryDirectory(prefix="ken-visual-") as profile:
        cmd = [chrome, "--headless", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
               "--mute-audio", "--hide-scrollbars", "--force-device-scale-factor=1", f"--window-size={w},{h}",
               # WebGL without a GPU: Chrome's software renderer, so 3D scenes draw on any laptop or server
               "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
               f"--virtual-time-budget={wait}", f"--screenshot={out.resolve()}", url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=wait / 1000 + 60)
    if not out.exists():
        raise SystemExit(f"Chrome didn't draw {html.name}:\n{(r.stderr or r.stdout)[-1500:]}")


def steps_in(html: Path) -> int:
    return max([int(n) for n in re.findall(r'data-(?:step|only)="(\d+)"', html.read_text())] or [0])


def to_mp4(frames: list[Path], out: Path, hold: float, size: str) -> None:
    """Each step held `hold` seconds with a short crossfade between them, ending on the full picture."""
    fade = 0.45
    args, chain, last = [], [], "0:v"
    for f in frames:
        args += ["-loop", "1", "-t", f"{hold + fade:.2f}", "-i", str(f)]
    for i in range(1, len(frames)):
        tag = f"x{i}"
        chain.append(f"[{last}][{i}:v]xfade=transition=fade:duration={fade}:offset={i * hold:.2f}[{tag}]")
        last = tag
    w, h = size.split("x")
    chain.append(f"[{last}]scale={w}:{h},format=yuv420p,fps=30[out]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex", ";".join(chain), "-map", "[out]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


def new(folder: Path, three_d: bool) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / "visual.html"
    if dest.exists():
        raise SystemExit(f"{dest} already exists: edit it instead (that's how follow ups work).")
    shutil.copyfile(HERE / ("template-3d.html" if three_d else "template.html"), dest)
    print(dest)


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    if argv[0] == "new":
        return new(Path(argv[1]), "--3d" in argv)
    opt = lambda name, default: argv[argv.index(name) + 1] if name in argv else default
    target = Path(argv[0])
    html = target / "visual.html" if target.is_dir() else target
    if not html.exists():
        raise SystemExit(f"No {html}. Start one with: python3 {Path(__file__).name} new <folder>")
    size, wait, hold = opt("--size", "1080x1350"), int(opt("--wait", "4000")), float(opt("--hold", "2.6"))
    chrome, started = find_chrome(), time.time()
    png = html.with_suffix(".png")
    n = steps_in(html) if "--steps" in argv else 0
    jobs = [(png, None)] + [(html.parent / f"step-{i}.png", i) for i in range(1, n + 1)]
    for out, _ in jobs:
        out.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
        list(pool.map(lambda j: shoot(chrome, html, j[0], size, wait, j[1]), jobs))
    print(png)
    if n:
        frames = [out for out, _ in jobs[1:]] + [png]
        mp4 = html.parent / "steps.mp4"
        to_mp4(frames, mp4, hold, size)
        print(*frames[:-1], mp4, sep="\n")
    print(f"drawn in {time.time() - started:.1f} s")


if __name__ == "__main__":
    main(sys.argv[1:])
