"""Remotion for Ken's videos, set up the first time someone wants one, never bundled: its own Node,
the kit's packages and the Chrome Remotion renders with, all in <home>/tools (about 850 MB).

    ken --video-setup            set it up (safe to repeat; a no-op once it's ready)
    ken --video-new <folder>     a new video project there, sharing the kit's packages
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

NODE_VERSION = "22.20.0"
NODE_SHA256 = {  # from https://nodejs.org/dist/v22.20.0/SHASUMS256.txt
    "darwin-arm64": "cc04a76a09f79290194c0646f48fec40354d88969bec467789a5d55dd097f949",
    "darwin-x64": "00df9c5df3e4ec6848c26b70fb47bf96492f342f4bed6b17f12d99b3a45eeecc",
    "linux-arm64": "4181609e03dcb9880e7e5bf956061ecc0503c77a480c6631d868cb1f65a2c7dd",
    "linux-x64": "eeaccb0378b79406f2208e8b37a62479c70595e20be6b659125eb77dd1ab2a29",
}
SOURCE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "video" / "kit"


def node_dir(home: Path) -> Path:
    return home / "tools" / "node"


def kit_dir(home: Path) -> Path:
    return home / "tools" / "remotion-kit"


def ready(home: Path) -> bool:
    stamp = kit_dir(home) / ".ready"
    return stamp.exists() and stamp.read_text() == _kit_version()


def _kit_version() -> str:
    return hashlib.sha256((SOURCE / "package-lock.json").read_bytes()).hexdigest()[:16]


def _platform() -> str:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(platform.machine())
    if not system or not arch:
        raise SystemExit(f"Video tools aren't available on {platform.system()} {platform.machine()} yet.")
    return f"{system}-{arch}"


def _env(home: Path) -> dict:
    return {**os.environ, "PATH": f"{node_dir(home) / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "npm_config_update_notifier": "false", "npm_config_fund": "false", "npm_config_audit": "false"}


def _download(url: str, dest: Path, say) -> None:
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        total, done, shown = int(r.headers.get("Content-Length") or 0), 0, -1
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            pct = done * 100 // total if total else 0
            if pct // 25 != shown:
                shown = pct // 25
                say(f"  downloading Node… {pct}%")


def ensure_node(home: Path, say=print) -> Path:
    """Ken's own Node, so it never depends on (or changes) one they installed themselves."""
    node = node_dir(home) / "bin" / "node"
    if node.exists():
        return node
    plat = _platform()
    name = f"node-v{NODE_VERSION}-{plat}"
    node_dir(home).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=node_dir(home).parent) as tmp:
        archive = Path(tmp) / f"{name}.tar.gz"
        _download(f"https://nodejs.org/dist/v{NODE_VERSION}/{name}.tar.gz", archive, say)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != NODE_SHA256[plat]:
            raise SystemExit("The Node download didn't match its checksum, so it wasn't installed. Try again later.")
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")
        shutil.rmtree(node_dir(home), ignore_errors=True)
        (Path(tmp) / name).rename(node_dir(home))
    return node


def setup(home: Path, say=print) -> Path:
    """Node, the kit's packages and Remotion's Chrome. Each step is skipped when it's already done."""
    if ready(home):
        say(f"Video tools are ready: {kit_dir(home)}")
        return kit_dir(home)
    say("Setting up video tools (one time, a few minutes)…")
    ensure_node(home, say)
    kit = kit_dir(home)
    kit.mkdir(parents=True, exist_ok=True)
    for f in ("package.json", "package-lock.json"):
        shutil.copyfile(SOURCE / f, kit / f)
    npm = node_dir(home) / "bin" / "npm"
    say("  installing Remotion…")
    _run([str(npm), "ci", "--no-audit", "--no-fund", "--loglevel=error"], kit, home)
    say("  getting the browser Remotion renders with…")
    _run([str(node_dir(home) / "bin" / "npx"), "remotion", "browser", "ensure"], kit, home)
    (kit / ".ready").write_text(_kit_version())
    say(f"Video tools are ready: {kit}")
    return kit


def _run(cmd: list[str], cwd: Path, home: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, env=_env(home), capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{' '.join(Path(c).name for c in cmd[:2])} failed:\n{(r.stderr or r.stdout)[-2000:]}")


def new_project(home: Path, folder: Path, say=print) -> Path:
    """A video project: the kit's template (reel.json, src/, public/), using the kit's packages."""
    kit = setup(home, say)
    folder.mkdir(parents=True, exist_ok=True)
    for item in (SOURCE / "template").iterdir():
        dest = folder / item.name
        if dest.exists():
            continue  # never overwrite their edits
        (shutil.copytree if item.is_dir() else shutil.copyfile)(item, dest)
    (folder / "public").mkdir(exist_ok=True)
    modules = folder / "node_modules"
    if not os.path.lexists(modules):
        modules.symlink_to(kit / "node_modules", target_is_directory=True)
    say(f"New video project: {folder}\nRender it with: cd {folder} && npx remotion render src/index.ts Reel out.mp4")
    return folder


def main(argv: list[str], home: Path) -> None:
    say = lambda line: print(line, flush=True)
    if argv[0] == "--video-setup":
        setup(home, say)
    elif argv[0] == "--video-new" and len(argv) > 1:
        new_project(home, Path(os.path.expanduser(argv[1])).resolve(), say)
    else:
        raise SystemExit(__doc__)


def path_entry(home: Path) -> str:
    return str(node_dir(home) / "bin")


if __name__ == "__main__":
    main(sys.argv[1:], Path(os.environ.get("KEN_HOME", Path.home() / ".ken")))
