"""Hosted Ken: creator storage, the person's own space in their host's cloud storage (ken-hosted src/media.ts, R2).

A Ken computer's disk is small (5 to 20 GB) and a creator's full-quality footage is not, so originals and finished
videos can live in storage instead. Ken asks with the token it checks in with (KEN_KEEPALIVE_TOKEN), and only ever
reaches its own person's files.

  ken --media put <file> [--keep]     store a file (in 64 MB pieces), print its key; --keep for finished work
  ken --media get <key> <dest>        bring a file back (or --range START-END for part of it)
  ken --media link <key> [days]       a share link anyone can open, for 1 to 30 days (default 7)
  ken --media list                    what's stored, and how much of the plan's allowance is used
  ken --media delete <key>            remove a file
  ken --media keep <key>              keep it past the 90 days originals are kept for

The final render can read a stored original straight from its URL (`ken --media url <key>` prints a URL ffmpeg can
open, valid for an hour), so a 4 GB original never has to fit on this disk.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PART = 64 * 1024 * 1024
TIMEOUT = 600


def site() -> str:
    url = os.environ.get("KEN_KEEPALIVE_URL", "")
    return url.rsplit("/box/", 1)[0] if url.startswith("https://") and "/box/" in url else ""


def available() -> bool:
    return bool(site()) and bool(os.environ.get("KEN_KEEPALIVE_TOKEN"))


def _req(method: str, path: str, body: bytes | dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    h = {"Authorization": f"Bearer {os.environ['KEN_KEEPALIVE_TOKEN']}", **(headers or {})}
    if isinstance(body, dict):
        h["Content-Type"] = "application/json"
    return urllib.request.urlopen(urllib.request.Request(f"{site()}{path}", data=data, method=method, headers=h), timeout=TIMEOUT)


def _json(method: str, path: str, body: bytes | dict | None = None, headers: dict | None = None) -> dict:
    try:
        with _req(method, path, body, headers) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read()).get("error")
        except Exception:
            said = None
        raise RuntimeError(said or f"storage answered {e.code}") from None


def put(path: Path, keep: bool = False) -> dict:
    """Store a file; returns {key, size}. Pieces are sent one at a time, each tried up to 3 times."""
    size = path.stat().st_size
    started = _json("POST", "/box/media/start", {"name": path.name, "size": size, "keep": keep})
    key, upload, part = started["key"], started["upload"], int(started.get("part") or PART)
    parts = []
    q = lambda n: f"/box/media/part?key={urllib.parse.quote(key)}&upload={urllib.parse.quote(upload)}&n={n}"
    with open(path, "rb") as f:
        n = 1
        while chunk := f.read(part):
            for tries in range(3):
                try:
                    parts.append(_json("PUT", q(n), chunk, {"Content-Type": "application/octet-stream"}))
                    break
                except Exception:
                    if tries == 2:
                        raise
            n += 1
    return _json("POST", "/box/media/done", {"key": key, "upload": upload, "parts": parts})


def get(key: str, dest: Path, byte_range: str = "") -> Path:
    headers = {"Range": f"bytes={byte_range}"} if byte_range else {}
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _req("GET", f"/box/media/file?key={urllib.parse.quote(key)}", headers=headers) as r, open(dest, "wb") as out:
        while chunk := r.read(1024 * 1024):
            out.write(chunk)
    return dest


def link(key: str, days: float = 7) -> str:
    return _json("POST", "/box/media/link", {"key": key, "days": days})["url"]


def main(args: list[str]) -> int:
    if not available():
        print("Storage is only on Ken in the cloud (kencomputer.dev). Here, files stay on this computer.")
        return 1
    cmd = args[0] if args else "list"
    try:
        if cmd == "put" and len(args) >= 2:
            r = put(Path(args[1]).expanduser(), keep="--keep" in args)
            print(f"stored {r['key']} ({r['size'] / 1048576:.1f} MB)")
        elif cmd == "get" and len(args) >= 3:
            rng = args[args.index("--range") + 1] if "--range" in args else ""
            print(f"saved {get(args[1], Path(args[2]).expanduser(), rng)}")
        elif cmd in ("link", "url") and len(args) >= 2:
            print(link(args[1], float(args[2]) if cmd == "link" and len(args) > 2 else (1 / 24 if cmd == "url" else 7)))
        elif cmd == "list":
            r = _json("GET", "/box/media/list")
            gb = 1024 ** 3
            print(f"{r['used'] / gb:.2f} GB of {r['allowance'] / gb:.0f} GB used. Originals are kept {r['keep_days']} days unless kept.")
            for f in r["files"]:
                print(f"{f['key']}  {f['size'] / 1048576:.1f} MB{'  (kept)' if f['keep'] else ''}")
        elif cmd == "delete" and len(args) >= 2:
            _json("POST", "/box/media/delete", {"key": args[1]})
            print("deleted")
        elif cmd == "keep" and len(args) >= 2:
            _json("POST", "/box/media/keep", {"key": args[1], "keep": True})
            print("kept")
        else:
            print(__doc__.split("\n\n")[1])
            return 2
    except Exception as e:
        print(f"Storage: {e}")
        return 1
    return 0
