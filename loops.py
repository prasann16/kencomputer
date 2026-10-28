"""Open loops: what's waiting on the user, read from memory/open-loops.md.

Ken keeps the file (numbered items under '## Waiting on ...' headings); the harness mirrors it
in one pinned Telegram message and closes items when the user taps ✅ or answers. Parsing is
forgiving: Ken writes this file by hand, so headings and numbering drift.
"""

from __future__ import annotations

import html
import re
import time

HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
# "3. text", "3) text", "- 3. text", "**3.** text", "#3 text", "- [ ] 3. text"
ITEM = re.compile(r"^\s*(?:[-*+]\s+)?(?:\[[ xX]?\]\s*)?(?:\*\*)?(?:#(\d{1,3})|(\d{1,3})[.):])(?:\*\*)?\s+(.+)$")
BULLET = re.compile(r"^\s*[-*+]\s+(?:\[ ?\]\s*)?(.+)$")
DONE = re.compile(r"^\s*(?:~~|\(closed|\(done|✅|\[x\])", re.I)
MD_PATH = re.compile(r"(?:~|/)[^\s`'\"()<>\[\]*]*?\.md\b")
MAX_BUTTONS = 8
MAX_READ_PINS = 5


def kind(title: str) -> str:
    """What a heading holds: 'you', 'others', 'ken', 'read', 'closed' or 'other' (parked, notes)."""
    t = title.lower()
    if t.startswith(("closed", "done")) or "closed" in t.split()[:2]:
        return "closed"
    if "to read" in t or t.startswith(("unread", "read")):
        return "read"
    if "waiting on" in t or "waiting for" in t or t.startswith(("needs you", "for you", "your turn")):
        if "other" in t or "them" in t.split():
            return "others"
        if re.search(r"\b(ken|me|assistant)\b", t):
            return "ken"
        return "you"
    return "other"


def parse(text: str) -> list[dict]:
    """Sections in order: {title, kind, start, items: [{num, text, line, end}]}. Lines are 0-based;
    an item ends where the next item, bullet or heading starts (indented lines belong to it)."""
    sections: list[dict] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        h = HEADING.match(line)
        if h:
            sections.append({"title": h.group(1), "kind": kind(h.group(1)), "start": i, "items": []})
            continue
        if not sections or not line.strip():
            continue
        items = sections[-1]["items"]
        m = ITEM.match(line)
        b = None if m else BULLET.match(line)
        if m or (b and not line.startswith((" ", "\t"))):
            body = (m.group(3) if m else b.group(1)).strip()
            items.append({"num": int(m.group(1) or m.group(2)) if m else None, "text": body, "line": i, "end": i + 1,
                          "done": bool(DONE.match(body))})
        elif items and line[:1] in (" ", "\t") and items[-1]["end"] == i:
            items[-1]["end"] = i + 1  # a wrapped line of the item above
    return sections


def open_items(text: str, which: tuple = ("you", "read")) -> list[dict]:
    return [dict(it, kind=s["kind"]) for s in parse(text) if s["kind"] in which for it in s["items"] if not it["done"]]


def render(text: str) -> tuple[str, list[list[tuple[str, str]]]]:
    """The pinned message (Telegram HTML) and its keyboard: rows of (label, callback data)."""
    sections = parse(text)
    out, nums = ["📌 <b>Open loops</b>"], []
    for s in sections:
        items = [it for it in s["items"] if not it["done"]]
        if s["kind"] in ("you", "read") and items:
            out.append(f"\n<b>{html.escape(s['title'])}</b>")
            for it in items:
                label = f"{it['num']}." if it["num"] is not None else "•"
                out.append(f"{label} {html.escape(short(it['text']))}")
                if it["num"] is not None:
                    nums.append(it["num"])
    others = sum(len([it for it in s["items"] if not it["done"]]) for s in sections if s["kind"] == "others")
    if len(out) == 1:
        out.append("Nothing waiting on you.")
    if others:
        out.append(f"\n⏳ {others} waiting on others")
    nums = list(dict.fromkeys(nums))[:MAX_BUTTONS]
    buttons = [(f"✅ {n}", f"loop:{n}") for n in nums]
    return "\n".join(out), [buttons[i:i + 4] for i in range(0, len(buttons), 4)]


def short(s: str, limit: int = 110) -> str:
    s = re.sub(r"\*\*|__|`", "", s)
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def close(text: str, num: int, when: float | None = None) -> tuple[str, str] | None:
    """Move open item `num` (from a waiting or to-read section) under '## Closed <date>'.
    Returns (new text, the item's text), or None if there's no such open item."""
    hit = next((it for it in open_items(text, ("you", "read", "others", "ken")) if it["num"] == num), None)
    if hit is None:
        return None
    return _move_to_closed(text, hit, when), hit["text"]


def close_matching(text: str, needle: str, when: float | None = None) -> tuple[str, str] | None:
    """Close the first open to-read item that mentions `needle` (a file path)."""
    hit = next((it for it in open_items(text, ("read", "you")) if needle in it["text"]), None)
    if hit is None:
        return None
    return _move_to_closed(text, hit, when), hit["text"]


def _move_to_closed(text: str, item: dict, when: float | None) -> str:
    lines = text.splitlines()
    del lines[item["line"]:item["end"]]
    title = "Closed " + time.strftime("%b %-d", time.localtime(when or time.time()))
    entry = f"- {item['text']}"
    body = "\n".join(lines)
    for s in parse(body):
        if s["kind"] == "closed" and s["title"].lower() == title.lower():
            at = s["items"][-1]["end"] if s["items"] else s["start"] + 1
            lines.insert(at, entry)
            return "\n".join(lines).rstrip("\n") + "\n"
    return body.rstrip("\n") + f"\n\n## {title}\n{entry}\n"


def add_read(text: str, title: str, path: str) -> tuple[str, int]:
    """Add '<title>: <path>' under '## To read' with the next free number; returns (new text, number)."""
    sections = parse(text)
    used = [it["num"] for s in sections for it in s["items"] if it["num"] is not None]
    num = max(used, default=0) + 1
    entry = f"{num}. {title.strip()}: {path}"
    lines = text.splitlines()
    for s in sections:
        if s["kind"] == "read":
            at = s["items"][-1]["end"] if s["items"] else s["start"] + 1
            lines.insert(at, entry)
            return "\n".join(lines).rstrip("\n") + "\n", num
    # A new To read section goes before Closed, so it sits with the other open work.
    closed = next((s["start"] for s in sections if s["kind"] in ("closed", "other")), None)
    block = ["## To read", entry, ""]
    if closed is None:
        return (text.rstrip("\n") + "\n\n" + "\n".join(block)).rstrip("\n") + "\n", num
    lines[closed:closed] = block
    return "\n".join(lines).rstrip("\n") + "\n", num


def deliverables(report: str) -> list[str]:
    """The .md files a background run says it made, not Ken's own memory and soul files."""
    found = []
    for p in MD_PATH.findall(report or ""):
        p = p.rstrip(".,;:")
        if "/memory/" in p or p.endswith(("/SOUL.md", "/CLAUDE.md", "/AGENTS.md")) or p in found:
            continue
        found.append(p)
    return found


def mentioned(paths: list[str], message: str) -> str | None:
    """The first of `paths` Ken's message carries (full path or file name)."""
    for p in paths:
        name = p.rsplit("/", 1)[-1]
        if p in message or name in message:
            return p
    return None


def cap(pins: list[dict], keep: int = MAX_READ_PINS) -> tuple[list[dict], list[dict]]:
    """Oldest first: (pins kept, pins to unpin)."""
    if len(pins) <= keep:
        return pins, []
    return pins[-keep:], pins[:-keep]
