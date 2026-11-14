"""Photos and videos shared from the phone app: the originals stay on the phone, Ken keeps a small thumbnail (a few
frames for a video) and its own note about each one, so it can find them, make posts from them and suggest clean-ups.

~/.ken/photos/
  index.json          {id: entry}: when it was taken, where, its size; Ken's note, subjects and quality once it looked
  thumbs/<id>-<n>.jpg  ~512px: one for a photo, a few frames for a video
  originals/<id>.<ext> full-size files Ken asked the phone for (for a post), removed once that's done
  asks.json           {"originals": [ids], "remove": {id: why}}: what Ken wants from the phone, read by the app

Ken uses them through the photos tools (photos_server); the phone app through web.py's /api/photos."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path

from places import place_name

LOOK_AT_ONCE = 12  # thumbnails per photos_look call (a video's frames count as one)
FIND_LIMIT = 40


def _clean_id(raw: str) -> str:
    """The phone's asset id ("ABC-123/L0/001", "content://…/45") as a file-safe id."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(raw)).strip("-")
    return s[-80:] or "photo"


class Photos:
    def __init__(self, home: Path):
        self.dir = Path(home) / "photos"
        self.thumbs = self.dir / "thumbs"
        self.originals = self.dir / "originals"
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ storage

    def _read(self, name: str, empty):
        try:
            return json.loads((self.dir / name).read_text())
        except (OSError, ValueError):
            return empty

    def _write(self, name: str, data) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{name}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(self.dir / name)

    def index(self) -> dict:
        return self._read("index.json", {})

    def asks(self) -> dict:
        a = self._read("asks.json", {})
        return {"originals": list(a.get("originals") or []), "remove": dict(a.get("remove") or {})}

    def add(self, meta: dict, frames: list[bytes]) -> dict:
        """One photo or video from the phone: its details and thumbnail(s). Sharing it again only refreshes it."""
        pid = _clean_id(meta.get("id") or meta.get("asset_id") or "")
        kind = "video" if meta.get("kind") == "video" else "photo"
        self.thumbs.mkdir(parents=True, exist_ok=True)
        for old in self.thumbs.glob(f"{pid}-*.jpg"):
            old.unlink(missing_ok=True)
        for n, data in enumerate(frames[:6]):
            (self.thumbs / f"{pid}-{n}.jpg").write_bytes(data)
        num = lambda k: meta.get(k) if isinstance(meta.get(k), (int, float)) else None
        with self._lock:
            idx = self.index()
            old = idx.get(pid, {})
            entry = {
                "id": pid, "asset_id": str(meta.get("asset_id") or meta.get("id") or ""), "kind": kind,
                "taken_at": num("taken_at"), "lat": num("lat"), "lon": num("lon"),
                "width": num("width"), "height": num("height"), "duration": num("duration"),
                "frame_times": [t for t in (meta.get("frame_times") or []) if isinstance(t, (int, float))][:6],
                "name": str(meta.get("name") or "")[:120], "frames": min(len(frames), 6),
                "place": place_name(num("lat"), num("lon")),
                "favorite": bool(meta.get("favorite")), "screenshot": bool(meta.get("screenshot")),
                "subtypes": [str(t)[:20] for t in (meta.get("subtypes") or []) if isinstance(t, str)][:8],
                "shared_at": old.get("shared_at") or time.time(),
            }
            for k in ("note", "subjects", "quality", "original"):  # what Ken already worked out stays
                if k in old:
                    entry[k] = old[k]
            idx[pid] = entry
            self._write("index.json", idx)
        return entry

    def remove(self, pid: str) -> bool:
        """Forget one (they took it back, or deleted it on the phone): its thumbnails, original and note."""
        with self._lock:
            idx = self.index()
            entry = idx.pop(pid, None)
            if entry is None:
                return False
            self._write("index.json", idx)
            asks = self.asks()
            asks["originals"] = [i for i in asks["originals"] if i != pid]
            asks["remove"].pop(pid, None)
            self._write("asks.json", asks)
        for f in self.thumbs.glob(f"{pid}-*.jpg"):
            f.unlink(missing_ok=True)
        for f in self.originals.glob(f"{pid}.*"):
            f.unlink(missing_ok=True)
        return True

    def thumb(self, pid: str, n: int = 0) -> Path | None:
        p = self.thumbs / f"{_clean_id(pid)}-{int(n)}.jpg"
        return p if p.is_file() else None

    def original_path(self, pid: str, filename: str) -> Path:
        """Where the full-size file Ken asked for goes (the phone sends it; web.py writes it there)."""
        if pid not in self.index():
            raise KeyError(pid)
        self.originals.mkdir(parents=True, exist_ok=True)
        return self.originals / f"{pid}{(Path(filename).suffix or '.jpg').lower()[:8]}"

    def got_original(self, pid: str, dest: Path) -> None:
        with self._lock:
            idx = self.index()
            if pid in idx:
                idx[pid]["original"] = dest.name
                self._write("index.json", idx)
            asks = self.asks()
            asks["originals"] = [i for i in asks["originals"] if i != pid]
            self._write("asks.json", asks)

    def unnoted(self) -> list[str]:
        return [pid for pid, e in self.index().items() if not e.get("note")]

    # ------------------------------------------------------------------ what Ken does

    def find(self, query: str = "", since: str = "", until: str = "", kind: str = "", limit: int = FIND_LIMIT) -> str:
        idx = self.index()
        if not idx:
            return "Nothing has been shared from the phone yet. They can add photos and videos in the Ken phone app (Photos)."
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
        lo, hi = _day(since), _day(until, end=True)
        rows = []
        for e in idx.values():
            if kind in ("photo", "video") and e.get("kind") != kind:
                continue
            t = e.get("taken_at") or e.get("shared_at") or 0
            if (lo and t < lo) or (hi and t > hi):
                continue
            text = " ".join([e.get("note") or "", " ".join(e.get("subjects") or []), e.get("name") or "", e.get("place") or "",
                             "favorite" if e.get("favorite") else "", "screenshot" if e.get("screenshot") else "",
                             " ".join(e.get("subtypes") or [])]).lower()
            score = sum(w in text for w in words) if words else 1
            if words and not score:
                continue
            rows.append((score, t, e))
        rows.sort(key=lambda r: (-r[0], -r[1]))
        waiting = sum(1 for e in idx.values() if not e.get("note"))
        out = [f"{len(rows)} of {len(idx)} shared match" + ("" if len(rows) <= limit else f"; the best {limit}:")]
        for _, t, e in rows[:limit]:
            out.append(_line(e))
        if waiting:
            out.append(f"({waiting} not looked at yet, so they have no note: photos_look them to see what they are.)")
        return "\n".join(out)

    def events(self, limit: int = 30) -> str:
        """Shared photos grouped into events: runs close in time and place ("Lisbon, Portugal · Jun 12-19, 2026 · 38,
        a trip"). A new event starts after 48 hours without photos or when the place changes; photos with no location
        stay in the event around them. Home is where most of them were taken."""
        idx = list(self.index().values())
        if not idx:
            return "Nothing has been shared from the phone yet."
        places = [e.get("place") for e in idx if e.get("place")]
        home = max(set(places), key=places.count) if places else ""
        dated = sorted((e for e in idx if e.get("taken_at")), key=lambda e: e["taken_at"])
        runs: list[dict] = []
        for e in dated:
            t, place = e["taken_at"], e.get("place") or ""
            last = runs[-1] if runs else None
            if last and t - last["end"] <= 48 * 3600 and (not place or not last["place"] or place == last["place"]):
                last["end"], last["n"] = t, last["n"] + 1
                last["kinds"].add(e.get("kind"))
                last["place"] = last["place"] or place
            else:
                runs.append({"place": place, "start": t, "end": t, "n": 1, "kinds": {e.get("kind")}})
        out = [f"{len(runs)} events (home looks like {home})." if home else f"{len(runs)} events."]
        for r in reversed(runs[-limit:]):
            a, b = time.localtime(r["start"]), time.localtime(r["end"])
            span = time.strftime("%b %d", a) + ("" if time.strftime("%Y%m%d", a) == time.strftime("%Y%m%d", b) else time.strftime(" - %b %d", b))
            kinds = " and ".join(sorted(k + "s" for k in r["kinds"] if k))
            trip = ", a trip" if r["place"] and home and r["place"] != home else ""
            out.append(f"{r['place'] or 'no location'} · {span}, {time.strftime('%Y', b)} · {r['n']} {kinds}{trip} "
                       f"(photos_find since {time.strftime('%Y-%m-%d', a)} until {time.strftime('%Y-%m-%d', b)})")
        undated = len(idx) - len(dated)
        if undated:
            out.append(f"{undated} with no date.")
        return "\n".join(out)

    def look(self, ids: list[str]) -> list[dict]:
        """The thumbnails, as images Ken sees, each with its line of details first."""
        idx, content = self.index(), []
        for pid in [str(i) for i in ids][:LOOK_AT_ONCE]:
            e = idx.get(pid)
            if not e:
                content.append({"type": "text", "text": f"{pid}: not found (photos_find lists what's shared)."})
                continue
            content.append({"type": "text", "text": _line(e)})
            for n in range(e.get("frames") or 1):
                p = self.thumb(pid, n)
                if p:
                    if e.get("kind") == "video" and e.get("frame_times"):
                        times = e["frame_times"]
                        content.append({"type": "text", "text": f"  frame at {times[n] if n < len(times) else '?'}s"})
                    content.append({"type": "image", "data": base64.b64encode(p.read_bytes()).decode(), "mimeType": "image/jpeg"})
        return content

    def note(self, pid: str, note: str, subjects: list[str] | None = None, quality: int | None = None) -> str:
        with self._lock:
            idx = self.index()
            if pid not in idx:
                return f"{pid}: not found."
            idx[pid]["note"] = str(note).strip()[:1500]
            if subjects is not None:
                idx[pid]["subjects"] = [str(s)[:40] for s in subjects][:12]
            if isinstance(quality, (int, float)):
                idx[pid]["quality"] = max(1, min(10, int(quality)))
            self._write("index.json", idx)
        return f"Saved the note for {pid}."

    def want_originals(self, ids: list[str]) -> str:
        """Full-size files for a post: the ones already here, and the rest asked of the phone."""
        self.prune_originals()
        idx, here, asked = self.index(), [], []
        with self._lock:
            asks = self.asks()
            for pid in [str(i) for i in ids]:
                e = idx.get(pid)
                if not e:
                    continue
                if e.get("original") and (self.originals / e["original"]).is_file():
                    here.append(str(self.originals / e["original"]))
                elif pid not in asks["originals"]:
                    asks["originals"].append(pid)
                    asked.append(pid)
                else:
                    asked.append(pid)
            self._write("asks.json", asks)
        out = []
        if here:
            out.append("Here already:\n" + "\n".join(here))
        if asked:
            out.append(f"Asked the phone for {len(asked)}. The Ken phone app sends them while it's open (they arrive in "
                       f"{self.originals}). If they don't come within a minute or two, tell them to open Ken on their phone.")
        return "\n\n".join(out) or "None of those ids are shared."

    def keep(self, pid: str) -> None:
        """They'd rather keep one Ken suggested removing."""
        with self._lock:
            asks = self.asks()
            asks["remove"].pop(pid, None)
            self._write("asks.json", asks)

    def suggest_removing(self, items: list[dict]) -> str:
        """Clean-up: suggest deleting some from the phone. Only shown to them; nothing is deleted until they say yes
        on the phone, which also asks them to confirm."""
        idx = self.index()
        with self._lock:
            asks = self.asks()
            n = 0
            for it in items:
                pid = str(it.get("id", ""))
                if pid in idx:
                    asks["remove"][pid] = str(it.get("why") or "")[:200]
                    n += 1
            self._write("asks.json", asks)
        return (f"Suggested {n} to remove. They see them under Photos in the Ken phone app and choose there; the phone "
                "asks them to confirm before anything is deleted.")

    def prune_originals(self, older_than: float = 86400) -> None:
        """Full-size copies are only for the post being made: gone a day later, so the disk doesn't fill up."""
        cutoff, gone = time.time() - older_than, set()
        for f in self.originals.glob("*"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                gone.add(f.name)
        if gone:
            with self._lock:
                idx = self.index()
                for e in idx.values():
                    if e.get("original") in gone:
                        e.pop("original", None)
                self._write("index.json", idx)


def _day(s: str, end: bool = False) -> float | None:
    if not s:
        return None
    try:
        t = time.mktime(time.strptime(s[:10], "%Y-%m-%d"))
        return t + 86399 if end else t
    except ValueError:
        return None


def _line(e: dict) -> str:
    t = e.get("taken_at") or e.get("shared_at")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "unknown date"
    what = f"video {round(e['duration'])}s" if e.get("kind") == "video" and e.get("duration") else e.get("kind", "photo")
    where = f" · {e['place']}" if e.get("place") else (
        f" · at {e['lat']:.4f},{e['lon']:.4f}" if e.get("lat") is not None and e.get("lon") is not None else "")
    marks = "".join([" · ★ favorite" if e.get("favorite") else "", " · screenshot" if e.get("screenshot") else "",
                     f" · {', '.join(e['subtypes'])}" if e.get("subtypes") else ""])
    size = f" · {e['width']}x{e['height']}" if e.get("width") and e.get("height") else ""
    q = f" · quality {e['quality']}/10" if e.get("quality") else ""
    note = f" · {e['note']}" if e.get("note") else " · (not looked at yet)"
    return f"{e['id']} · {when} · {what}{size}{where}{marks}{q}{note}"


# ---------------------------------------------------------------------- the tools Ken uses

INDEX_PROMPT = ("{n} photos/videos were just shared with you from their phone. Look at them with photos_look, a dozen "
                "at a time (photos_find lists the ones without a note), and save a note for each with photos_note: "
                "what's in it and who, the setting, any text worth finding later (a receipt, a ticket, a sign), for a "
                "video what happens when (by the frame times), which parts look usable and whether someone talks "
                "to the camera, whether it would crop well to vertical (9:16); plus a few subjects and a quality score. This is just for you to find them later: "
                "don't message them about it.")


def photos_tools(home: Path):
    """The photos tools, for an in-process MCP server (photos_server) and the stdio one (ken_mcp)."""
    from claude_agent_sdk import tool

    p = Photos(home)

    def text(s: str) -> dict:
        return {"content": [{"type": "text", "text": s}]}

    async def find(a):
        return text(p.find(str(a.get("query") or ""), str(a.get("since") or ""), str(a.get("until") or ""),
                           str(a.get("kind") or ""), int(a.get("limit") or FIND_LIMIT)))

    async def look(a):
        return {"content": p.look(list(a.get("ids") or []))}

    async def events(a):
        return text(p.events())

    async def note(a):
        return text(p.note(str(a.get("id", "")), str(a.get("note", "")), a.get("subjects"), a.get("quality")))

    async def originals(a):
        return text(p.want_originals(list(a.get("ids") or [])))

    async def remove(a):
        return text(p.suggest_removing(list(a.get("items") or [])))

    ids = {"type": "array", "items": {"type": "string"}}
    return [
        tool("photos_find", "The photos and videos they shared from their phone (the originals stay on the phone): "
             "search your notes about them by words (also the place name, \"favorite\", \"screenshot\"), dates "
             "(YYYY-MM-DD) and kind (photo or video). Each line: id, when it was taken, kind, size, place, whether "
             "it's a favorite or a screenshot, quality and your note.",
             {"type": "object", "properties": {"query": {"type": "string"}, "since": {"type": "string"}, "until": {"type": "string"},
                                               "kind": {"type": "string"}, "limit": {"type": "integer"}}})(find),
        tool("photos_events", "Their shared photos and videos grouped into events: a run close in time and place, like "
             "a trip, a wedding or a day out, with where, when, how many and whether it was away from home.",
             {"type": "object", "properties": {}})(events),
        tool("photos_look", f"See shared photos and videos: a small picture of each (a few frames of a video, with their "
             f"times). Up to {LOOK_AT_ONCE} ids at a time.",
             {"type": "object", "properties": {"ids": ids}, "required": ["ids"]})(look),
        tool("photos_note", "Save your note about one shared photo or video, so photos_find finds it later: what's in "
             "it, for a video what happens when; subjects in your own words; quality 1-10.",
             {"type": "object", "properties": {"id": {"type": "string"}, "note": {"type": "string"},
                                               "subjects": ids, "quality": {"type": "integer"}}, "required": ["id", "note"]})(note),
        tool("photos_originals", "Get the full-size files of shared photos or videos, to make something from them (a "
             "post, a reel). The phone sends them while the Ken app is open on it.",
             {"type": "object", "properties": {"ids": ids}, "required": ["ids"]})(originals),
        tool("photos_suggest_removing", "Clean-up: suggest photos or videos they could delete from their phone "
             "(blurry, duplicates, accidental shots), each with a short why. They choose in the Ken phone app, and "
             "the phone asks them to confirm before deleting anything.",
             {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
                 "id": {"type": "string"}, "why": {"type": "string"}}, "required": ["id"]}}}, "required": ["items"]})(remove),
    ]


def photos_server(home: Path):
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server("photos", tools=photos_tools(home))
