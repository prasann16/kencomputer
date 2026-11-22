"""Ken for Mac, linked to their Ken in the cloud (kencomputer.dev), so the cloud Ken can use things on this Mac that
they allow. Two halves, one file:

- On the Mac (`MacLink`): linked with a short code from the cloud Settings. It only ever connects OUT to the site: it
  asks for work (a long poll) and sends each answer back. No port is opened. What it will answer is decided here, by
  switches they turn on in Settings → Cloud, all off at first; the site's word is never trusted for that.
- In the cloud (`cloud_main`, `ken --mac …`): Ken's command line for asking the Mac, through the site.

Anything that would send or change something on the Mac must ask them on the Mac first (`Cap.changes`); nothing like
that exists yet, and such a capability is refused until it has its own approval prompt.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("ken")

SITE = "https://kencomputer.dev"
MAX_FILE = 1024 * 1024  # bytes one file.get sends (the site carries answers up to ~1.5 MB)
MAX_LIST = 500
MAX_FOUND = 50
MAX_SCANNED = 20_000

SWITCHES = {
    "files": "Files in folders you choose",
}


class Refused(Exception):
    """Something the Mac won't do: said to the cloud Ken as it is, in plain words."""


@dataclass
class Cap:
    switch: str  # the switch in Settings → Cloud that must be on
    run: Callable[[dict], object]  # runs in a thread
    changes: bool = False  # sends or changes something: needs their OK on the Mac first


def computer_name() -> str:
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5).stdout.strip()
            if name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    return socket.gethostname().split(".")[0] or "Mac"


class MacLink:
    def __init__(self, home: Path, site: str | None = None, name: Callable[[], str] = computer_name) -> None:
        self.file = home / "mac-link.json"
        self.site = (site or os.environ.get("KEN_SITE_URL") or SITE).rstrip("/")
        self.name = name
        self.connected = False
        self.problem = ""
        self.ask: Callable[[str, str], Awaitable[bool]] | None = None  # an approval prompt on the Mac, once one is needed
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self.caps: dict[str, Cap] = {
            "files.folders": Cap("files", self._folders),
            "files.list": Cap("files", self._list),
            "files.find": Cap("files", self._find),
            "files.get": Cap("files", self._get),
        }

    # ------------------------------------------------------------------ what's saved: the token and their switches

    def state(self) -> dict:
        try:
            st = json.loads(self.file.read_text())
        except (OSError, ValueError):
            st = {}
        st.setdefault("switches", {})
        st.setdefault("folders", [])
        return st

    def _save(self, st: dict) -> None:
        tmp = self.file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the token: only this user may read it
        with os.fdopen(fd, "w") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, self.file)

    def linked(self) -> bool:
        return bool(self.state().get("token"))

    def switches(self) -> dict[str, bool]:
        on = self.state()["switches"]
        return {k: bool(on.get(k)) for k in SWITCHES}

    def caps_on(self) -> list[str]:
        return sorted(k for k, v in self.switches().items() if v)

    def status(self) -> dict:
        st = self.state()
        return {"linked": bool(st.get("token")), "account": st.get("account", ""), "since": st.get("since"),
                "online": self.connected, "problem": self.problem,
                "switches": [{"id": k, "label": label, "on": self.switches()[k]} for k, label in SWITCHES.items()],
                "folders": st["folders"]}

    def set_switch(self, which: str, on: bool) -> dict:
        if which not in SWITCHES:
            raise ValueError("There's no such switch.")
        st = self.state()
        st["switches"][which] = bool(on)
        self._save(st)
        return self.status()

    def set_folders(self, folders: list) -> dict:
        clean: list[str] = []
        for f in folders:
            p = Path(str(f)).expanduser()
            if not p.is_absolute() or not p.is_dir():
                raise ValueError(f"{f} isn't a folder on this Mac.")
            if p.resolve() == Path.home().resolve() or str(p.resolve()) == "/":
                raise ValueError("Pick a folder inside your home folder, not all of it.")
            if str(p) not in clean:
                clean.append(str(p))
        st = self.state()
        st["folders"] = clean
        self._save(st)
        return self.status()

    # ------------------------------------------------------------------ linking

    async def link(self, code: str, client=None) -> dict:
        import httpx
        code = "".join(ch for ch in str(code).upper() if ch.isalnum())
        if len(code) < 6:
            raise ValueError("Type the code shown in Ken on kencomputer.dev → Settings → Your Mac.")
        try:
            async with (client or httpx.AsyncClient(timeout=20)) as c:
                r = await c.post(f"{self.site}/mac/link", json={"code": code, "name": self.name()})
        except httpx.HTTPError:
            raise ValueError("Couldn't reach kencomputer.dev. Check the internet connection and try again.") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or not body.get("token"):
            raise ValueError(body.get("error") or "That didn't work. Get a new code and try again.")
        st = self.state()
        st.update(token=body["token"], account=body.get("account", ""), since=time.time())
        self._save(st)
        self.problem = ""
        self._wake.set()
        log.info("mac link: linked")
        return self.status()

    async def unlink(self, client=None) -> dict:
        import httpx
        token = self.state().get("token")
        if token:
            try:
                async with (client or httpx.AsyncClient(timeout=15)) as c:
                    await c.post(f"{self.site}/mac/unlink", headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError:
                log.warning("mac link: couldn't tell the site about the unlink; the link is forgotten here")
        self._forget("")
        return self.status()

    def _forget(self, why: str) -> None:
        st = self.state()
        for k in ("token", "account", "since"):
            st.pop(k, None)
        self._save(st)
        self.connected = False
        self.problem = why

    # ------------------------------------------------------------------ answering the cloud Ken

    async def handle(self, call: dict) -> dict:
        cap = self.caps.get(str(call.get("cap", "")))
        if cap is None:
            return {"ok": False, "error": "This Mac can't do that. Ken for Mac may need an update."}
        if not self.switches().get(cap.switch):
            return {"ok": False, "error": f"“{SWITCHES[cap.switch]}” is off in Ken for Mac. They can turn it on in Ken for Mac → Settings → Cloud."}
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        try:
            if cap.changes:
                if self.ask is None or not await self.ask(call.get("cap", ""), json.dumps(args)[:500]):
                    return {"ok": False, "error": "They didn't allow that on their Mac."}
            return {"ok": True, "result": await asyncio.to_thread(cap.run, args)}
        except Refused as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # anything else stays on the Mac's log; the cloud hears a plain line
            log.warning("mac link: %s failed: %s", call.get("cap"), e)
            return {"ok": False, "error": "That didn't work on the Mac."}

    async def run(self, client=None) -> None:
        """While linked: ask the site for work, answer it, again. Off the internet: wait and try again, never stop."""
        import httpx
        own = client is None
        client = client or httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15))
        delay = 5
        try:
            while True:
                token = self.state().get("token")
                if not token:
                    self.connected = False
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                auth = {"Authorization": f"Bearer {token}"}
                try:
                    r = await client.post(f"{self.site}/mac/poll", json={"caps": self.caps_on()}, headers=auth)
                    if r.status_code == 401:
                        log.info("mac link: the site no longer knows this Mac; unlinked")
                        self._forget("This Mac was unlinked from your Ken in the cloud.")
                        continue
                    r.raise_for_status()
                    self.connected, self.problem, delay = True, "", 5
                    for call in r.json().get("calls", []):
                        task = asyncio.create_task(self._answer(client, auth, call))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                except (httpx.HTTPError, ValueError) as e:
                    self.connected, self.problem = False, "Can't reach kencomputer.dev right now. Trying again…"
                    log.info("mac link: poll failed (%s); again in %ds", type(e).__name__, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
        finally:
            if own:
                await client.aclose()

    async def _answer(self, client, auth: dict, call: dict) -> None:
        answer = await self.handle(call)
        try:
            await client.post(f"{self.site}/mac/result", json={"id": call.get("id"), **answer}, headers=auth)
        except Exception as e:
            log.warning("mac link: couldn't send an answer: %s", e)

    # ------------------------------------------------------------------ files in the folders they chose

    def _inside(self, path: str) -> Path:
        """The real file or folder behind `path`, if it's inside a folder they chose (links followed first)."""
        if not path:
            raise Refused("Say which file or folder.")
        try:
            real = Path(str(path)).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise Refused(f"There's no {path} on their Mac.") from None
        for f in self.state()["folders"]:
            try:
                root = Path(f).expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if real == root or root in real.parents:
                return real
        raise Refused("That isn't in a folder they've shared. They choose folders in Ken for Mac → Settings → Cloud.")

    def _folders(self, args: dict) -> dict:
        return {"folders": [{"name": Path(f).name, "path": f} for f in self.state()["folders"]]}

    def _list(self, args: dict) -> dict:
        folder = self._inside(str(args.get("path", "")))
        if not folder.is_dir():
            raise Refused(f"{folder.name} is a file, not a folder.")
        items = []
        for p in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if p.name.startswith("."):
                continue
            try:
                s = p.stat()
            except OSError:
                continue
            items.append({"name": p.name, "path": str(p), "kind": "folder" if p.is_dir() else "file",
                          "size": s.st_size if p.is_file() else None, "modified": int(s.st_mtime)})
            if len(items) >= MAX_LIST:
                break
        return {"path": str(folder), "items": items, "more": len(items) >= MAX_LIST}

    def _find(self, args: dict) -> dict:
        words = [w.lower() for w in str(args.get("words", "")).split() if w]
        if not words:
            raise Refused("Say what to look for.")
        found, scanned = [], 0
        for f in self.state()["folders"]:
            for dirpath, dirs, files in os.walk(Path(f).expanduser()):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in files:
                    scanned += 1
                    if not name.startswith(".") and all(w in name.lower() for w in words):
                        p = Path(dirpath) / name
                        try:
                            s = p.stat()
                        except OSError:
                            continue
                        found.append({"name": name, "path": str(p), "size": s.st_size, "modified": int(s.st_mtime)})
                    if len(found) >= MAX_FOUND or scanned >= MAX_SCANNED:
                        return {"found": found, "more": True}
        return {"found": found, "more": False}

    def _get(self, args: dict) -> dict:
        path = self._inside(str(args.get("path", "")))
        if not path.is_file():
            raise Refused(f"{path.name} is a folder, not a file.")
        size = path.stat().st_size
        if size > MAX_FILE:
            raise Refused(f"{path.name} is {size // (1024 * 1024)} MB, too big to fetch from the Mac (1 MB at most for now).")
        return {"name": path.name, "size": size, "data": base64.b64encode(path.read_bytes()).decode()}


# ---------------------------------------------------------------------- the cloud side: `ken --mac …`

def _site() -> tuple[str, str] | None:
    url, token = os.environ.get("KEN_KEEPALIVE_URL", ""), os.environ.get("KEN_KEEPALIVE_TOKEN", "")
    if not token or not url.startswith(("https://", "http://127.0.0.1", "http://localhost")) or "/box/" not in url:
        return None
    return url.rsplit("/box/", 1)[0] + "/box/mac/call", token


def ask_mac(cap: str, args: dict | None = None, timeout: int = 60) -> dict:
    """Ask their linked Mac, through the site. Returns its answer, or raises Refused with a plain reason."""
    import urllib.error
    import urllib.request
    where = _site()
    if not where:
        raise Refused("Their Mac can only be reached from Ken in the cloud.")
    req = urllib.request.Request(where[0], data=json.dumps({"cap": cap, "args": args or {}}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {where[1]}", "Content-Type": "application/json", "User-Agent": "Ken"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            answer = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            answer = json.loads(e.read())
        except ValueError:
            answer = {"ok": False, "error": f"The site answered {e.code}."}
    except (OSError, ValueError):
        raise Refused("Couldn't reach their Mac just now. Try once more.") from None
    if not answer.get("ok"):
        raise Refused(answer.get("error") or "Their Mac didn't answer.")
    return answer.get("result") or {}


USAGE = """ken --mac folders                  the folders they shared from their Mac
ken --mac list <folder>            what's in one
ken --mac find <words…>            files whose names have all these words
ken --mac get <file> [--to <dir>]  a copy of one file here (1 MB at most)"""


def cloud_main(args: list[str]) -> int:
    if not args:
        print(USAGE)
        return 2
    what, rest = args[0], args[1:]
    try:
        if what == "folders":
            out = ask_mac("files.folders")
            print("\n".join(f"{f['name']}: {f['path']}" for f in out.get("folders", [])) or "No folders shared yet.")
        elif what == "list" and rest:
            out = ask_mac("files.list", {"path": " ".join(rest)})
            for it in out.get("items", []):
                print(f"{'📁' if it['kind'] == 'folder' else '  '} {it['path']}" + (f"  ({it['size']} bytes)" if it.get("size") is not None else ""))
            if out.get("more"):
                print("(more not shown)")
        elif what == "find" and rest:
            out = ask_mac("files.find", {"words": " ".join(rest)})
            print("\n".join(f"{f['path']}  ({f['size']} bytes)" for f in out.get("found", [])) or "Nothing found.")
        elif what == "get" and rest:
            to = Path.cwd()
            if "--to" in rest:
                i = rest.index("--to")
                to, rest = Path(rest[i + 1]).expanduser(), rest[:i] + rest[i + 2:]
            out = ask_mac("files.get", {"path": " ".join(rest)})
            to.mkdir(parents=True, exist_ok=True)
            dest = to / Path(out["name"]).name
            dest.write_bytes(base64.b64decode(out["data"]))
            print(f"Saved {dest}")
        else:
            print(USAGE)
            return 2
    except Refused as e:
        print(str(e))
        return 1
    return 0
