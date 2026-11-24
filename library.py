"""Their Library (hosted Ken): the photos and videos they put in, kept in cloud storage by the site that hosts this
computer, never on this computer's small disk. This computer reaches only its own person's Library, with the token it
checks in with (KEN_KEEPALIVE_TOKEN); it holds no storage keys.

Two jobs:
- New items: make a small thumbnail and read when and where each was taken (the place named here, offline, by
  places.py), so the Library shows them and Ken can find them. Runs while Ken is awake (`process_new`).
- Ken's command line (`ken --library …`): find items by place, date, kind or Ken's own notes; fetch the few a task needs
  (a whole file, or just part of a long video); note what's in one; save something Ken made into their Library.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PIECE = 64 * 1024 * 1024
THUMB_PX = 512


def site() -> tuple[str, str] | None:
    """Where the site is and this computer's token; None off hosting."""
    url, token = os.environ.get("KEN_KEEPALIVE_URL", ""), os.environ.get("KEN_KEEPALIVE_TOKEN", "")
    if not token or not url.startswith(("https://", "http://127.0.0.1", "http://localhost")) or "/box/" not in url:
        return None
    return url.rsplit("/box/", 1)[0] + "/box/library", token


class Off(Exception):
    """No Library for this person (not hosted, or not switched on for them)."""


def call(path: str, body: dict | bytes | None = None, method: str | None = None, headers: dict | None = None, timeout: int = 60):
    where = site()
    if not where:
        raise Off()
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(f"{where[0]}/{path}", data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Authorization": f"Bearer {where[1]}", "User-Agent": "Ken",
                                          **({"Content-Type": "application/json"} if isinstance(body, dict) else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404 and path.split("?")[0] in ("todo", "find"):
            raise Off() from None
        msg = e.read().decode(errors="replace")
        try:
            msg = json.loads(msg).get("error", msg)
        except ValueError:
            pass
        raise RuntimeError(msg[:300]) from None
    return json.loads(raw) if raw[:1] in (b"{", b"[") else raw


def file_url(item_id: str) -> tuple[str, str]:
    where = site()
    if not where:
        raise Off()
    return f"{where[0]}/file?id={item_id}", where[1]


def on() -> bool:
    try:
        call("find?limit=1")
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ new items: thumbnail, when and where

def _iso6709(s: str) -> tuple[float, float] | None:
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", s or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def _when(s: str) -> int | None:
    """A camera's date ("2026:10:12 18:03:11", "2026-10-12T18:03:11.000000Z") as ms; None when it isn't one."""
    s = (s or "").strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(s, fmt)
            if d.year < 1990:
                return None
            return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _dms(value: str, ref: str) -> float | None:
    """EXIF GPS ("38/1, 43/1, 2034/100") as degrees; negative for S and W."""
    try:
        parts = [p.strip() for p in value.split(",")]
        nums = [float(a) / float(b) if "/" in p else float(p) for p in parts for a, b in [p.split("/") if "/" in p else (p, "1")]]
        deg = nums[0] + (nums[1] if len(nums) > 1 else 0) / 60 + (nums[2] if len(nums) > 2 else 0) / 3600
        return -deg if ref.strip().upper() in ("S", "W") else deg
    except (ValueError, IndexError, ZeroDivisionError):
        return None


def video_facts(url: str, token: str) -> dict:
    """Duration, size, when and where a video was taken, read straight from storage (only the parts ffprobe needs)."""
    out = subprocess.run(["ffprobe", "-v", "quiet", "-headers", f"Authorization: Bearer {token}\r\n", "-print_format", "json",
                          "-show_format", "-show_streams", url], capture_output=True, text=True, timeout=120)
    info = json.loads(out.stdout or "{}")
    fmt, tags = info.get("format", {}), (info.get("format", {}).get("tags") or {})
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    facts: dict = {"kind": "video"}
    if fmt.get("duration"):
        facts["duration"] = round(float(fmt["duration"]), 1)
    w, h = video.get("width"), video.get("height")
    rotate = str((video.get("tags") or {}).get("rotate", "")) or next((str(d.get("rotation", "")) for d in video.get("side_data_list", []) if "rotation" in d), "")
    if w and h:
        facts["width"], facts["height"] = (h, w) if rotate.lstrip("-") in ("90", "270") else (w, h)
    when = _when(tags.get("com.apple.quicktime.creationdate", "")) or _when(tags.get("creation_time", ""))
    if when:
        facts["taken_at"] = when
    spot = _iso6709(tags.get("com.apple.quicktime.location.ISO6709", "") or tags.get("location", ""))
    if spot:
        facts["lat"], facts["lon"] = spot
    return facts


def photo_facts(path: Path) -> dict:
    """Size, when and where a photo was taken, from its EXIF."""
    fields = ["%w", "%h", "%[EXIF:DateTimeOriginal]", "%[EXIF:GPSLatitude]", "%[EXIF:GPSLatitudeRef]", "%[EXIF:GPSLongitude]", "%[EXIF:GPSLongitudeRef]"]
    tool = shutil.which("magick") or shutil.which("identify")
    if not tool:
        return {"kind": "photo"}
    cmd = [tool, "identify"] if tool.endswith("magick") else [tool]
    out = subprocess.run([*cmd, "-format", "|".join(fields) + "\n", f"{path}[0]"], capture_output=True, text=True, timeout=60).stdout
    parts = (out.splitlines() or [""])[0].split("|")
    facts: dict = {"kind": "photo"}
    if len(parts) == 7:
        if parts[0].isdigit() and parts[1].isdigit():
            facts["width"], facts["height"] = int(parts[0]), int(parts[1])
        if _when(parts[2]):
            facts["taken_at"] = _when(parts[2])
        lat, lon = _dms(parts[3], parts[4]) if parts[3] else None, _dms(parts[5], parts[6]) if parts[5] else None
        if lat is not None and lon is not None and (lat, lon) != (0, 0):
            facts["lat"], facts["lon"] = lat, lon
    return facts


def make_thumb(source: str | Path, out: Path, video: bool = False, token: str = "", at: float = 0, size: int = 0) -> bool:
    """A JPEG about THUMB_PX on its short side, cropped by the app to a square. A video's from a frame a little in."""
    px = size or THUMB_PX
    if video:
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-headers", f"Authorization: Bearer {token}\r\n", "-i", str(source),
               "-frames:v", "1", "-vf", f"scale='if(gt(iw,ih),-2,{px})':'if(gt(iw,ih),{px},-2)'", "-q:v", "4", str(out)]
    else:
        tool = shutil.which("magick") or shutil.which("convert")
        if not tool:
            return False
        cmd = [tool, f"{source}[0]", "-auto-orient", "-thumbnail", f"{px}x{px}^", "-quality", "80", "-strip", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.is_file() and out.stat().st_size > 0


def process(item: dict, work: Path) -> None:
    """One new item: its thumbnail, and when and where it was taken (what the phone sent stays unless the file knows better)."""
    from places import place_name

    url, token = file_url(item["id"])
    thumb = work / f"{item['id']}.jpg"
    facts: dict = {}
    if item.get("kind") == "video":
        try:
            facts = video_facts(url, token)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            facts = {"kind": "video"}
        make_thumb(url, thumb, video=True, token=token, at=min(1.0, (facts.get("duration") or 2) / 3))
    else:
        local = work / f"{item['id']}{Path(item['name']).suffix.lower() or '.jpg'}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "Ken"})
        with urllib.request.urlopen(req, timeout=300) as r, open(local, "wb") as f:
            shutil.copyfileobj(r, f)
        if item.get("kind") == "photo":
            facts = photo_facts(local)
            make_thumb(local, thumb)
        local.unlink(missing_ok=True)
    lat, lon = facts.get("lat", item.get("lat")), facts.get("lon", item.get("lon"))
    place = place_name(lat, lon) if lat is not None and lon is not None else ""
    if thumb.is_file():
        call(f"thumb?id={item['id']}", thumb.read_bytes(), method="PUT", headers={"Content-Type": "image/jpeg"})
        thumb.unlink(missing_ok=True)
    call("meta", {"id": item["id"], **facts, **({"place": place} if place else {})})


def process_new(limit: int = 50, log=None) -> int | None:
    """Every new item still without a thumbnail, date and place. How many were done; None when there's no Library."""
    try:
        items = call("todo").get("items", [])
    except Off:
        return None
    done = 0
    with tempfile.TemporaryDirectory(prefix="ken-library-") as tmp:
        for item in items[:limit]:
            try:
                process(item, Path(tmp))
                done += 1
            except Exception as e:  # one bad file mustn't stop the rest; it's marked done so it isn't retried forever
                if log:
                    log.warning("library: %s failed: %s", item.get("name"), e)
                try:
                    call("meta", {"id": item["id"]})
                except Exception:
                    pass
    return done


# ------------------------------------------------------------------ watching copies of chat videos

def _previews(path: str, data=None, method: str = "GET", headers: dict | None = None, timeout: int = 30) -> dict:
    where = site()
    if not where:
        raise Off()
    url = where[0].rsplit("/library", 1)[0] + "/previews/" + path
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Bearer {where[1]}", "User-Agent": "Ken", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:  # not on for them
            raise Off() from None
        raise RuntimeError(f"{path.split('?')[0]}: {e.code}") from None


def name_watching_copy(key: str, refs: list[str]) -> None:
    """Which watching copy the chat's name for a video plays now (`refs`: a path as Ken named it, or
    chat:<pid>/<name>). Sent before the copy is made too, so a new version never plays the old one."""
    _previews("names", json.dumps({"key": key, "refs": refs}).encode(), "POST", {"Content-Type": "application/json"})


def store_watching_copy(copy: Path, refs: list[str], upload: bool = True) -> None:
    """A video's small watching copy (web.py _preview) kept by the site next to their Library, so the phone and the web
    play it from storage at once instead of waking this computer to stream it (ken-hosted previews.ts). Off (Library
    not on for them, or not hosted): raises Off."""
    key = copy.stem
    if upload and not _previews(f"has?key={key}").get("has"):
        with copy.open("rb") as f:
            _previews(f"file?key={key}", f, "PUT", {"Content-Type": "video/mp4", "Content-Length": str(copy.stat().st_size)}, timeout=300)
    if refs:
        name_watching_copy(key, refs)


# ------------------------------------------------------------------ Ken's command line

def _day(s: str) -> int | None:
    try:
        return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def describe(it: dict) -> str:
    when = datetime.fromtimestamp((it.get("taken_at") or 0) / 1000, tz=timezone.utc).strftime("%d %b %Y %H:%M") if it.get("taken_at") else "?"
    bits = [it["id"], it["kind"], when]
    if it.get("place"):
        bits.append(it["place"])
    if it.get("duration"):
        bits.append(f"{it['duration']:.0f}s")
    if it.get("width") and it.get("height"):
        bits.append("vertical" if it["height"] > it["width"] else "horizontal")
    bits.append(it["name"])
    if it.get("album"):
        bits.append(f"album: {it['album']}")
    line = " · ".join(str(b) for b in bits)
    return line + (f"\n    note: {it['note']}" if it.get("note") else "")


def main(args: list[str], home: Path) -> int:
    """ken --library find [words] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--kind photo|video] [--limit N]
       ken --library get <id> [<id> …] [--seconds A-B]   → a copy in ~/.ken/work/library/ (only part of a video with --seconds)
       ken --library thumb <id> [<id> …]                → its small thumbnail, to look at quickly
       ken --library note <id> <what's in it>
       ken --library add <file> [<file> …]              → saved into their Library
       ken --library show <id> [<id> …]                 → those photos or clips appear in the chat, for them to see"""
    if not args or args[0] in ("-h", "--help", "help"):
        print(main.__doc__)
        return 0
    cmd, rest = args[0], args[1:]
    try:
        if cmd == "find":
            q, opts, words = {}, {"--from": "from", "--to": "to", "--kind": "kind", "--limit": "limit"}, []
            i = 0
            while i < len(rest):
                if rest[i] in opts and i + 1 < len(rest):
                    v = rest[i + 1]
                    q[opts[rest[i]]] = str(_day(v) if opts[rest[i]] in ("from", "to") else v)
                    i += 2
                else:
                    words.append(rest[i])
                    i += 1
            if words:
                q["q"] = " ".join(words)
            r = call("find?" + urllib.parse.urlencode({k: v for k, v in q.items() if v not in ("None", "")}))
            items = r.get("items", [])
            print(f"{len(items)} found (of {r.get('total', '?')} in their Library):" if items else f"Nothing matched (their Library has {r.get('total', 0)}).")
            for it in items:
                print(describe(it))
            return 0
        if cmd in ("get", "thumb"):
            ids = [a for a in rest if re.fullmatch(r"[0-9a-f]{16}", a)]
            span = next((rest[i + 1] for i, a in enumerate(rest) if a == "--seconds" and i + 1 < len(rest)), "")
            dest = home / "work" / "library"
            dest.mkdir(parents=True, exist_ok=True)
            found = call("find?limit=500").get("items", []) if ids else []
            for item_id in ids:
                it = next((x for x in found if x["id"] == item_id), {"id": item_id, "name": item_id, "kind": "file"})
                url, token = file_url(item_id)
                if cmd == "thumb":
                    out = dest / f"{item_id}-thumb.jpg"
                    if it["kind"] == "video":
                        ok = make_thumb(url, out, video=True, token=token, at=1)
                    else:
                        original = dest / f"{item_id}-original{Path(it['name']).suffix}"
                        ok = _download(url, token, original) and make_thumb(original, out)
                        original.unlink(missing_ok=True)
                elif span:
                    out = dest / f"{Path(it['name']).stem}-{span.replace('-', 'to')}s.mp4"
                    a, _, b = span.partition("-")
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", a, "-to", b or a, "-headers", f"Authorization: Bearer {token}\r\n",
                                    "-i", url, "-c", "copy", str(out)], capture_output=True, timeout=600)
                    ok = out.is_file()
                else:
                    out = dest / it["name"]
                    ok = _download(url, token, out)
                print(f"{item_id}: {out}" if ok else f"{item_id}: couldn't fetch it")
            print("Delete these copies when the task is done; the originals stay in their Library.")
            return 0
        if cmd == "show" and rest:
            ids = [a for a in rest if re.fullmatch(r"[0-9a-f]{16}", a)][:12]
            found = {x["id"]: x for x in call("find?limit=500").get("items", [])}
            items = [{"id": i, "kind": found[i].get("kind", "photo"), "name": found[i].get("name", ""), "place": found[i].get("place") or ""} for i in ids if i in found]
            if not items:
                print("None of those are in their Library.")
                return 1
            req = home / "requests" / f"{int(time.time() * 1000)}-show.json"
            req.parent.mkdir(parents=True, exist_ok=True)
            tmp = req.with_suffix(".tmp")
            tmp.write_text(json.dumps({"type": "library_show", "agent": os.environ.get("KEN_CHAT", "home"), "items": items}))
            tmp.rename(req)
            print(f"Showing {len(items)} in the chat.")
            return 0
        if cmd == "note" and len(rest) >= 2:
            call("note", {"id": rest[0], "note": " ".join(rest[1:])})
            print("Noted.")
            return 0
        if cmd == "add" and rest:
            for f in rest:
                print(f"{f}: added ({add(Path(f))})")
            return 0
    except Off:
        print("Their Library isn't on for this computer.")
        return 1
    except RuntimeError as e:
        print(f"Couldn't: {e}")
        return 1
    print(main.__doc__)
    return 2


def _download(url: str, token: str, out: Path) -> bool:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "Ken"})
    with urllib.request.urlopen(req, timeout=900) as r, open(out, "wb") as f:
        shutil.copyfileobj(r, f)
    return out.is_file()


def add(path: Path) -> str:
    """Save a file into their Library (something Ken made, or a file they sent), in pieces."""
    size = path.stat().st_size
    started = call("start", {"name": path.name, "size": size})
    parts = []
    with open(path, "rb") as f:
        n = 1
        while True:
            chunk = f.read(PIECE)
            if not chunk and n > 1:
                break
            parts.append(call(f"part?id={started['id']}&upload={urllib.parse.quote(started['upload'])}&n={n}", chunk, method="PUT", timeout=600))
            n += 1
            if len(chunk) < PIECE:
                break
    call("done", {"id": started["id"], "upload": started["upload"], "parts": parts})
    return started["id"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], Path(os.environ.get("KEN_HOME", str(Path.home() / ".ken")))))
