"""Hosted Ken: finished videos live in the person's Google Drive, not on the computer (5 GB disk, and Telegram stops at 50 MB).

Before a video edit, Ken runs `ken --video-drive check`: Drive connected, or the one-tap Connect link to give them.
After the render, `ken --video-drive save <folder>` puts reel.mp4 and the original (source.<ext>) in a "Ken videos"
folder in their Drive, checks Drive's copy is byte for byte the same (size and MD5), notes the link in the folder's
drive.json and in ~/.ken/drive-videos.jsonl, and only then deletes the copy here. props.json, the cut list and the
other small project files stay, so a re-edit is quick; `ken --video-drive fetch <folder>` brings the source back.

The apps tools can't take a big file directly (GOOGLEDRIVE_UPLOAD_FILE stops at 5 MB and needs a file already in
Composio's storage), so Drive fetches it instead: GOOGLEDRIVE_UPLOAD_FROM_URL with a link that works for one file,
for 30 minutes. The link is the host's /box/drive-file/<computer>/<token> (ken-hosted), which passes the request to
this computer's web app (/api/drive-file/<token>, web.py) with the computer's own keys; the token is in
drive-out.json here and nowhere else.

Or their own computer: `save <folder> --to=computer` (and the default, --to=auto, when Ken's page is open on a
computer) offers each file on that page, through the same one-file links. They tap Save; the page pulls the file over
HTTPS, saves it where they pick (Downloads to start), reads it back and sends its size and SHA-256 (browsers have no
MD5), and only a match deletes the copy here. Nothing on their computer listens or opens a port; the page asks, the
host passes it on. Phones and Telegram get Drive. Neither there: the files stay here and Ken says so.

KEN_DRIVE_FAKE=<dir> stands in a pretend Drive in that folder (tests, and trying it without a Google account): it
fetches the link just as Drive would, so everything but Google itself is exercised.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import sys
import time
import urllib.request
from pathlib import Path

import apps

FOLDER = "Ken videos"
LINK_TTL_S = 30 * 60
SLUG = "googledrive"
UPLOAD_TIMEOUT = 15 * 60  # Drive fetches the whole file inside the one call
MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v", ".webm": "video/webm", ".mkv": "video/x-matroska"}


def home() -> Path:
    return Path(os.environ.get("KEN_HOME", str(Path.home() / ".ken")))


def md5(path: Path) -> str:
    return _digest(path, hashlib.md5())


def sha256(path: Path) -> str:
    return _digest(path, hashlib.sha256())


def _digest(path: Path, h) -> str:
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------ the one-file links (web.py serves them)

def _links_path() -> Path:
    return home() / "drive-out.json"


def _links() -> dict:
    try:
        return json.loads(_links_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_links(links: dict) -> None:
    path = _links_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(links))
    tmp.chmod(0o600)
    tmp.replace(path)


def open_link(path: Path, now: float | None = None, **extra) -> str:
    """A token that lets Drive (or their own computer) fetch this one file for LINK_TTL_S. Old ones are dropped as new
    ones are made."""
    now = time.time() if now is None else now
    links = {t: v for t, v in _links().items() if v.get("until", 0) > now}
    token = secrets.token_urlsafe(32)
    links[token] = {"path": str(path.resolve()), "until": now + LINK_TTL_S, **extra}
    _save_links(links)
    return token


def close_link(token: str) -> None:
    links = _links()
    if links.pop(token, None) is not None:
        _save_links(links)


def linked_file(token: str, now: float | None = None) -> Path | None:
    """The file a token is for, while it's open and the file is still there (web.py's /api/drive-file/<token>)."""
    now = time.time() if now is None else now
    hit = _links().get(token) if token else None
    if not hit or hit.get("until", 0) <= now:
        return None
    path = Path(hit["path"])
    return path if path.is_file() else None


def computer_id() -> str:
    """This computer's id on its host: Agent37 names the machine after the instance (8ru69il3kd.agent37.app)."""
    return os.environ.get("KEN_INSTANCE_ID", "").strip() or socket.gethostname()


def file_url(token: str) -> tuple[str, dict]:
    """Where Drive fetches the file, and any header it needs. KEN_DRIVE_FILE_BASE points it elsewhere (a test that
    reaches this computer's web app directly, with its token)."""
    base = os.environ.get("KEN_DRIVE_FILE_BASE", "").rstrip("/")
    if base:
        tok = (home() / "web-token").read_text().strip() if (home() / "web-token").is_file() else ""
        return f"{base}/api/drive-file/{token}", ({"Authorization": f"Bearer {tok}"} if tok else {})
    site = os.environ["KEN_KEEPALIVE_URL"].rsplit("/box/", 1)[0]
    return f"{site}/box/drive-file/{computer_id()}/{token}", {}


# ------------------------------------------------------------------ Drive, through the apps tools (or a pretend one)

class DriveError(Exception):
    pass


def _fake_dir() -> Path | None:
    d = os.environ.get("KEN_DRIVE_FAKE", "").strip()
    return Path(d) if d else None


def _fake(tool: str, args: dict):
    """A pretend Drive in a folder: files are <id>.bin plus <id>.json. Only the calls this module makes."""
    root = _fake_dir()
    root.mkdir(parents=True, exist_ok=True)
    def meta(fid):
        return json.loads((root / f"{fid}.json").read_text())
    if tool == "GOOGLEDRIVE_FIND_FILE":
        hits = [m for m in (json.loads(p.read_text()) for p in root.glob("*.json"))
                if m.get("mimeType") == "application/vnd.google-apps.folder" and m["name"] == FOLDER]
        return {"data": {"files": hits}}
    if tool == "GOOGLEDRIVE_CREATE_FOLDER":
        fid = "fld_" + secrets.token_hex(6)
        (root / f"{fid}.json").write_text(json.dumps({"id": fid, "name": args["name"], "mimeType": "application/vnd.google-apps.folder"}))
        return {"data": {"id": fid}}
    if tool == "GOOGLEDRIVE_UPLOAD_FROM_URL":
        req = urllib.request.Request(args["source_url"], headers={**(args.get("source_headers") or {}), "User-Agent": "Google-Drive-Fetch"})
        fid = "file_" + secrets.token_hex(8)
        h, size = hashlib.md5(), 0
        with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as r, (root / f"{fid}.bin").open("wb") as out:
            for block in iter(lambda: r.read(1 << 20), b""):
                h.update(block); size += len(block); out.write(block)
        (root / f"{fid}.json").write_text(json.dumps({"id": fid, "name": args["name"], "mimeType": args.get("mime_type", ""),
                                                      "parents": [args.get("parent_folder_id")], "size": str(size), "md5Checksum": h.hexdigest(),
                                                      "webViewLink": f"https://drive.google.com/file/d/{fid}/view"}))
        return {"data": {"id": fid}}
    if tool == "GOOGLEDRIVE_GET_FILE_METADATA":
        return {"data": meta(args["fileId"])}
    if tool == "GOOGLEDRIVE_DOWNLOAD_FILE":
        return {"data": {"downloaded_file_content": {"s3url": (root / f"{args['file_id']}.bin").resolve().as_uri()}}}
    raise DriveError(f"pretend Drive has no {tool}")


def call(tool: str, args: dict, account: str = ""):
    if _fake_dir():
        return _fake(tool, args)
    item = {"tool_slug": tool, "arguments": args}
    if account:
        item["account"] = account
    answer = apps.mcp_call("COMPOSIO_MULTI_EXECUTE_TOOL", {"tools": [item], "thought": "Keep their edited video in their own Google Drive."})
    for d in apps._walk(answer):  # the executor answers ok with the tool's own failure inside
        if d.get("successful") is False or (d.get("error") and not d.get("data")):
            raise DriveError(str(d.get("error") or "Drive said no")[:300])
    return answer


def _first(answer, *keys):
    for d in apps._walk(answer):
        for k in keys:
            v = d.get(k)
            if v not in (None, "", []):
                return v
    return None


def connected() -> dict:
    """{"ok": True, "account": id-or-""} or {"ok": False, "connect_url": ...} from the host's Apps list."""
    if _fake_dir():
        return {"ok": True, "account": ""}
    data = apps.fetch("Google Drive")
    match = data.get("match") or {}
    if match.get("slug") == SLUG and match.get("connected"):
        drive = next((a for a in data.get("connected") or [] if a.get("slug") == SLUG), {})
        accounts = drive.get("account_list") or []
        return {"ok": True, "account": "", "accounts": len(accounts) or 1}
    return {"ok": False, "connect_url": match.get("connect_url") or data.get("apps_url", "")}


def folder_id(account: str = "") -> str:
    found = call("GOOGLEDRIVE_FIND_FILE", {"q": f"name = '{FOLDER}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"}, account)
    files = _first(found, "files")
    if isinstance(files, list) and files and files[0].get("id"):
        return files[0]["id"]
    made = call("GOOGLEDRIVE_CREATE_FOLDER", {"name": FOLDER}, account)
    fid = _first(made, "id")
    if not fid:
        raise DriveError("couldn't make the Ken videos folder")
    return fid


def upload(path: Path, account: str = "", parent: str = "") -> dict:
    """Drive's copy of `path`, checked byte for byte: {"id", "name", "link", "size", "md5"}. Raises DriveError if
    Drive's copy isn't the same (the local file is kept then)."""
    size, digest = path.stat().st_size, md5(path)
    token = open_link(path)
    try:
        url, headers = file_url(token)
        args = {"source_url": url, "name": path.name, "mime_type": MIME.get(path.suffix.lower(), "application/octet-stream")}
        if headers:
            args["source_headers"] = headers
        if parent:
            args["parent_folder_id"] = parent
        fid = _first(call("GOOGLEDRIVE_UPLOAD_FROM_URL", args, account), "id", "fileId", "file_id")
    finally:
        close_link(token)
    if not fid:
        raise DriveError("Drive didn't say where it put the file")
    meta = call("GOOGLEDRIVE_GET_FILE_METADATA", {"fileId": fid, "fields": "id,name,size,md5Checksum,webViewLink"}, account)
    got_size, got_md5 = _first(meta, "size"), _first(meta, "md5Checksum")
    if str(got_size) != str(size) or got_md5 != digest:
        raise DriveError(f"Drive's copy doesn't match ({got_size} bytes, md5 {got_md5}; here {size} bytes, md5 {digest})")
    return {"id": fid, "name": path.name, "link": _first(meta, "webViewLink") or f"https://drive.google.com/file/d/{fid}/view",
            "size": size, "md5": digest}


def _record(folder: Path, kind: str, saved: dict) -> None:
    note = folder / "drive.json"
    try:
        data = json.loads(note.read_text())
    except (OSError, ValueError):
        data = {}
    data[kind] = {**saved, "at": int(time.time())}
    note.write_text(json.dumps(data, indent=1))
    with (home() / "drive-videos.jsonl").open("a") as log:
        log.write(json.dumps({"folder": str(folder), "kind": kind, **saved, "at": int(time.time())}) + "\n")


def _source(folder: Path) -> Path | None:
    """The original the reel was cut from: video_kit keeps it as source.<ext> in the project folder."""
    found = [p for p in folder.glob("source.*") if p.suffix.lower() in MIME and p.is_file()]
    return found[0] if found else None


def save(folder: Path, with_source: bool = True, account: str = "", say=print) -> int:
    """reel.mp4 and the original into their Drive, each checked, then deleted here. cut.mp4 (the cut before graphics)
    goes too once both are safe: it's made again from the original and props.json's cuts."""
    reel = folder / "reel.mp4"
    todo = [("reel", reel)] if reel.is_file() else []
    src = _source(folder) if with_source else None
    if src:
        todo.append(("source", src))
    if not todo:
        say(f"Nothing to save: no reel.mp4 in {folder}.")
        return 1
    parent = folder_id(account)
    for kind, path in todo:
        try:
            saved = upload(path, account, parent)
        except Exception as exc:  # noqa: BLE001 - say it, keep the file
            say(f"{path.name} wasn't saved to their Drive ({exc}). It's still here at {path}; don't delete it. Tell them plainly.")
            return 1
        _record(folder, kind, saved)
        path.unlink()
        say(f"{kind}: saved to their Drive (\"{FOLDER}\" folder), checked byte for byte, deleted here. Link: {saved['link']}")
    if src and (folder / "cut.mp4").is_file():
        (folder / "cut.mp4").unlink()
    say("Give them the reel's link (not the file). props.json and the transcript stay here, so a change is quick: "
        f"`ken --video-drive fetch {folder}` brings the original back, then --video-cut with props.json's cuts and render.")
    return 0


# ------------------------------------------------------------------ or their own computer (web.py's /api/pickups)

PHONE_UA = ("Mobi", "Android", "iPhone", "iPad")


def _web(path: str, body: dict | None = None, timeout: float = 5):
    """This computer's own web app, with its token (the running Ken knows which pages are open)."""
    tok = (home() / "web-token").read_text().strip()
    port = os.environ.get("KEN_WEB_PORT", "7777")
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def computer_open() -> int:
    """How many of Ken's pages are open on a computer (not a phone) right now. 0 if the web app can't be asked."""
    try:
        return int(_web("/api/pickups/pages").get("computer", 0))
    except Exception:  # noqa: BLE001 - no app, no page
        return 0


def offer(folder: Path, with_source: bool = True, now: float | None = None) -> list[dict]:
    """A one-file link for the reel (and the original) that Ken's page on their computer can pull, each with the size
    and SHA-256 the saved copy has to match."""
    files = [("reel", folder / "reel.mp4")] if (folder / "reel.mp4").is_file() else []
    src = _source(folder) if with_source else None
    if src:
        files.append(("source", src))
    links = _links()
    stale = [t for t, v in links.items() if v.get("pickup") and Path(v["path"]).parent == folder.resolve()]
    if stale:  # offered again: the new links replace the old ones
        _save_links({t: v for t, v in links.items() if t not in stale})
    out = []
    for kind, path in files:
        info = {"pickup": kind, "name": path.name, "size": path.stat().st_size, "sha256": sha256(path), "md5": md5(path),
                "label": folder.name}
        out.append({"token": open_link(path, now, **info), **info})
    return out


def pickups(now: float | None = None) -> list[dict]:
    """What's waiting to be saved on their computer, for the page (no paths)."""
    now = time.time() if now is None else now
    return [{"token": t, "kind": v["pickup"], "name": v["name"], "size": v["size"], "sha256": v["sha256"],
             "label": v.get("label", ""), "until": int(v["until"])}
            for t, v in _links().items() if v.get("pickup") and v.get("until", 0) > now and Path(v["path"]).is_file()]


def picked_up(token: str, size, digest: str, how: str = "disk", where: str = "", now: float | None = None) -> dict:
    """Their computer says it has the file: a matching size and SHA-256 deletes the copy here; anything else keeps it."""
    now = time.time() if now is None else now
    hit = _links().get(token or "")
    if not hit or not hit.get("pickup") or hit.get("until", 0) <= now:
        return {"ok": False, "error": "This link has expired. Ask Ken to offer the video again."}
    path = Path(hit["path"])
    if not path.is_file():
        close_link(token)
        return {"ok": False, "error": "That file isn't on Ken's computer any more."}
    if str(size) != str(hit["size"]) or str(digest).lower() != hit["sha256"]:
        return {"ok": False, "error": "The copy on your computer doesn't match, so Ken kept its own. Try again."}
    folder = path.parent
    saved = {"name": hit["name"], "size": hit["size"], "md5": hit["md5"], "sha256": hit["sha256"], "where": "computer",
             "checked": how, "saved_as": str(where)[:200]}
    _record(folder, hit["pickup"], saved)
    path.unlink()
    close_link(token)
    note = json.loads((folder / "drive.json").read_text())
    if note.get("source", {}).get("where") == "computer" and (folder / "cut.mp4").is_file() and not any(
            v.get("pickup") and Path(v["path"]).parent == folder for v in _links().values()):
        (folder / "cut.mp4").unlink()
    return {"ok": True, "kind": hit["pickup"], "name": hit["name"]}


def save_to_computer(folder: Path, with_source: bool = True, say=print) -> int:
    offered = offer(folder, with_source)
    if not offered:
        say(f"Nothing to save: no reel.mp4 in {folder}.")
        return 1
    try:
        _web("/api/pickups/announce", {"label": folder.name})
    except Exception:  # noqa: BLE001 - an open page also finds it when it next loads
        pass
    names = ", ".join(f"{o['name']} ({o['size'] / 1e6:.0f} MB)" for o in offered)
    say(f"Offered on Ken's page on their computer: {names}. They tap Save there; each file is deleted here only once their "
        "computer has it and its size and SHA-256 match. Tell them in one line to tap Save on Ken's page on their computer. "
        f"The links close in {LINK_TTL_S // 60} minutes; anything not saved by then stays here "
        f"(`ken --video-drive status {folder}` shows what's left; then offer Drive with `save {folder} --to=drive`).")
    return 0


def status(folder: Path, say=print) -> int:
    try:
        note = json.loads((folder / "drive.json").read_text())
    except (OSError, ValueError):
        note = {}
    for kind, v in note.items():
        place = "their computer" if v.get("where") == "computer" else "their Drive " + v.get("link", "")
        say(f"{kind}: {v.get('name')} saved to {place}, checked, deleted here.")
    waiting = [p for p in pickups() if p["label"] == folder.name]
    for p in waiting:
        say(f"{p['kind']}: {p['name']} waiting for them to tap Save on their computer.")
    left = [p.name for p in (folder / "reel.mp4", _source(folder) or folder / "-") if p.is_file()]
    if left and not waiting:
        say(f"Still only here: {', '.join(left)}.")
    return 0


def fetch(folder: Path, account: str = "", say=print) -> int:
    """The source back from Drive for a re-edit, checked against the MD5 noted when it was saved."""
    try:
        saved = json.loads((folder / "drive.json").read_text())["source"]
    except (OSError, ValueError, KeyError):
        say("The original wasn't kept in their Drive for this video. Ask them to send it again.")
        return 1
    dest = folder / saved["name"]
    if dest.is_file() and md5(dest) == saved["md5"]:
        say(f"The original is already here: {dest}")
        return 0
    got = call("GOOGLEDRIVE_DOWNLOAD_FILE", {"file_id": saved["id"]}, account)
    url = _first(got, "s3url", "url", "download_url")
    if not url:
        say("Drive didn't hand the file back. Try once more; if it fails again, ask them to send it again.")
        return 1
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Ken"}), timeout=UPLOAD_TIMEOUT) as r, tmp.open("wb") as out:
        shutil.copyfileobj(r, out, 1 << 20)
    if md5(tmp) != saved["md5"]:
        tmp.unlink()
        say("The copy from Drive didn't match the one saved. Ask them to send the original again.")
        return 1
    tmp.replace(dest)
    say(f"The original is back: {dest}")
    return 0


def save_where(folder: Path, to: str = "auto", with_source: bool = True, account: str = "", say=print) -> int:
    """--to=computer, --to=drive, or auto: their computer when Ken's page is open on one, else Drive, else keep it."""
    if to != "drive":
        if computer_open():
            return save_to_computer(folder, with_source, say)
        if to == "computer":
            say("Ken's page isn't open on their computer right now, so trying their Drive instead.")
    c = connected()
    if c["ok"]:
        return save(folder, with_source, account, say)
    say("Neither place is ready: Ken's page isn't open on a computer and Google Drive isn't connected, so the video stays "
        f"here at {folder}. Tell them plainly, and that they can open Ken on their computer and tap Save, or connect "
        f"Drive with this link: {c.get('connect_url', '')}")
    return 1


def main(argv: list[str]) -> int:
    if not (_fake_dir() or (apps.hosted() and apps.mcp_ready())):
        print("Drive storage is for Ken in the cloud. Here, keep the video on this computer as usual.")
        return 1
    what = argv[0] if argv else ""
    try:
        if what == "check":
            c = connected()
            if c["ok"]:
                print("Google Drive is connected. Go ahead with the edit; the finished video goes to their Drive "
                      "(or to their computer if Ken's page is open there when it's done).")
            elif computer_open():
                print("Ken's page is open on their computer: go ahead with the edit. When it's done, `save` offers it "
                      "there to save. (Google Drive isn't connected; don't ask about it now.)")
            else:
                print("Google Drive isn't connected. Before editing, reply in one line asking them to connect it, so the "
                      f"video is kept in their own Drive instead of on this computer, with this link exactly as printed: {c['connect_url']}")
                print("Then stop. You'll be told when it's connected; then start the edit.")
            return 0
        if what in ("save", "fetch", "status") and len(argv) > 1:
            folder = Path(argv[1]).expanduser().resolve()
            opt = lambda name, default="": next((a.split("=", 1)[1] for a in argv[2:] if a.startswith(f"--{name}=")), default)  # noqa: E731
            account, with_source = opt("account"), "--reel-only" not in argv[2:]
            if what == "status":
                return status(folder)
            if what == "fetch":
                return fetch(folder, account)
            return save_where(folder, opt("to", "auto"), with_source, account)
    except Exception as exc:  # noqa: BLE001 - one plain line; Ken tells them
        print(f"Couldn't reach their Drive just now ({str(exc)[:200]}). Keep the video here and tell them plainly.")
        return 1
    print("Usage: ken --video-drive check | save <folder> [--to=auto|computer|drive] [--reel-only] [--account=<id>] | "
          "status <folder> | fetch <folder>")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
