"""Their WhatsApp messages, read-only, kept in Ken's own small store (~/.ken/whatsapp.db) for Ken to search.

Two ways in:
- Ken for Mac, while the "WhatsApp (read)" switch is on (mac_link.py): about every 5 minutes, new messages from
  WhatsApp Desktop's own database. It's copied first (with its -wal), and only the copy is opened, so WhatsApp is never
  touched. macOS only lets an app read it with Full Disk Access; without it they're told so in plain words.
- On any Ken: a WhatsApp "Export chat" (.zip or .txt), once, for older history (`ken --whatsapp import <file>`).

Sending is out of scope here: nothing in this file writes to WhatsApp, and none of it should. Sending would be a
separate Mac capability with its own approval prompt on the Mac (mac_link.Cap.changes).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

CORE_DATA_EPOCH = 978307200  # WhatsApp's dates are seconds since 2001-01-01 (Apple's Core Data), not 1970
FULL_DISK_ACCESS = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
MEDIA = {1: "photo", 2: "video", 3: "voice message", 4: "contact", 5: "location", 8: "document", 15: "sticker"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  source TEXT NOT NULL, key TEXT NOT NULL, chat TEXT NOT NULL, sender TEXT NOT NULL, from_me INTEGER NOT NULL,
  ts REAL NOT NULL, text TEXT NOT NULL, PRIMARY KEY (source, key)
);
CREATE INDEX IF NOT EXISTS messages_ts ON messages (ts);
CREATE INDEX IF NOT EXISTS messages_chat ON messages (chat, ts);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


class NeedsAccess(Exception):
    """macOS won't let Ken read WhatsApp's database: Full Disk Access is off for Ken."""


class NotThere(Exception):
    """WhatsApp Desktop isn't on this Mac (or hasn't been opened yet)."""


def desktop_db() -> Path:
    """Where WhatsApp Desktop keeps its messages on a Mac. Only ever looked at while their WhatsApp switch is on."""
    return Path.home() / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"


def open_store(home: Path) -> sqlite3.Connection:
    db = sqlite3.connect(home / "whatsapp.db")
    db.executescript(SCHEMA)
    return db


# ---------------------------------------------------------------------- WhatsApp Desktop (Mac)

def _copy(src: Path, into: Path) -> Path:
    """The database and its -wal/-shm, copied under the same names (the newest messages are often only in the -wal)."""
    # No exists() first: without Full Disk Access it can say False, and that would read as "no WhatsApp".
    try:
        dest = into / src.name
        shutil.copyfile(src, dest)
        for extra in ("-wal", "-shm"):
            side = src.with_name(src.name + extra)
            try:
                shutil.copyfile(side, into / side.name)
            except FileNotFoundError:
                pass
        return dest
    except FileNotFoundError:
        raise NotThere() from None
    except PermissionError:
        raise NeedsAccess() from None
    except OSError as e:
        if getattr(e, "errno", None) == 1:  # EPERM: macOS privacy protection
            raise NeedsAccess() from None
        raise


def _signature(src: Path) -> str:
    """Size and time of the database and its -wal, to tell whether anything changed."""
    parts = []
    for p in (src, src.with_name(src.name + "-wal")):
        try:
            st = p.stat()
            parts.append(f"{st.st_size}:{st.st_mtime_ns}")
        except FileNotFoundError:
            if p == src:
                raise NotThere() from None
            parts.append("-")
        except PermissionError:
            raise NeedsAccess() from None
        except OSError as e:
            if getattr(e, "errno", None) == 1:
                raise NeedsAccess() from None
            raise
    return " ".join(parts)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def sync_desktop(home: Path, src: Path | None = None, batch: int = 5000) -> int:
    """New messages from WhatsApp Desktop since the last time (by Z_PK) into Ken's store. Returns how many."""
    src = src or desktop_db()
    store = open_store(home)
    try:
        last = int((store.execute("SELECT v FROM kv WHERE k='desktop_pk'").fetchone() or ["0"])[0])
        # Unchanged since last time (the database and its -wal): nothing new, and nothing to copy.
        seen = _signature(src)
        if seen == (store.execute("SELECT v FROM kv WHERE k='desktop_seen'").fetchone() or [""])[0]:
            store.execute("INSERT OR REPLACE INTO kv VALUES ('desktop_synced', ?)", (str(time.time()),))
            store.commit()
            return 0
        with tempfile.TemporaryDirectory(prefix="ken-wa-") as tmp:
            copy = _copy(src, Path(tmp))
            wa = sqlite3.connect(copy)  # a plain open of the copy, so its -wal is read too
            try:
                msg, chat = _columns(wa, "ZWAMESSAGE"), _columns(wa, "ZWACHATSESSION")
                if not {"Z_PK", "ZTEXT", "ZMESSAGEDATE", "ZCHATSESSION"} <= msg:
                    raise RuntimeError("WhatsApp's database isn't laid out as expected (a newer WhatsApp?)")
                pick = lambda cols, name, alt="NULL": name if name in cols else alt
                member = "ZGROUPMEMBER" in msg and "ZCONTACTNAME" in _columns(wa, "ZWAGROUPMEMBER")
                sql = (f"SELECT m.Z_PK, m.ZMESSAGEDATE, m.ZTEXT, {pick(msg, 'ZISFROMME', '0')}, {pick(msg, 'ZMESSAGETYPE', '0')}, "
                       f"c.{pick(chat, 'ZPARTNERNAME')}, c.{pick(chat, 'ZCONTACTJID')}, m.{pick(msg, 'ZFROMJID')}, "
                       + ("g.ZCONTACTNAME, g.ZMEMBERJID " if member else "NULL, NULL ")
                       + "FROM ZWAMESSAGE m LEFT JOIN ZWACHATSESSION c ON c.Z_PK = m.ZCHATSESSION "
                       + ("LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER " if member else "")
                       + "WHERE m.Z_PK > ? ORDER BY m.Z_PK LIMIT ?")
                added = 0
                while True:
                    rows = wa.execute(sql, (last, batch)).fetchall()
                    if not rows:
                        break
                    out = []
                    for pk, when, text, mine, kind, partner, jid, from_jid, member_name, member_jid in rows:
                        last = pk
                        if not text:
                            if not kind or kind not in MEDIA:
                                continue  # system notices, calls, deleted messages
                            text = f"[{MEDIA[kind]}]"
                        chat_name = partner or _number(jid) or "Unknown chat"
                        if mine:
                            sender = "me"
                        elif jid and str(jid).endswith("@g.us"):
                            sender = member_name or _number(member_jid) or _number(from_jid) or "someone"
                        else:
                            sender = chat_name
                        out.append(("desktop", str(pk), chat_name, sender, 1 if mine else 0, float(when or 0) + CORE_DATA_EPOCH, text))
                    store.executemany("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?)", out)
                    store.execute("INSERT OR REPLACE INTO kv VALUES ('desktop_pk', ?)", (str(last),))
                    store.commit()
                    added += len(out)
            finally:
                wa.close()
        store.execute("INSERT OR REPLACE INTO kv VALUES ('desktop_synced', ?)", (str(time.time()),))
        store.execute("INSERT OR REPLACE INTO kv VALUES ('desktop_seen', ?)", (seen,))
        store.commit()
        return added
    finally:
        store.close()


def _number(jid) -> str:
    """'447700900123@s.whatsapp.net' → '+447700900123'; a group's id → ''."""
    if not jid or str(jid).endswith("@g.us"):
        return ""
    head = str(jid).split("@")[0]
    return "+" + head if head.isdigit() else head


# ---------------------------------------------------------------------- an "Export chat" file (any Ken)

# [31/12/2024, 21:41:05] Sam: hi     (iPhone)        31/12/2024, 21:41 - Sam: hi     (Android)
LINE = re.compile(r"^\[?(\d{1,4})[./-](\d{1,2})[./-](\d{2,4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp]\.?\s?[Mm]\.?)?\]?\s*(?:-\s+)?(.*)$")
INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‬  ﻿"), " ")


def _read_export(path: Path) -> tuple[str, str]:
    """The chat's text and its name (from the file's name), from a .zip (read in memory, never unpacked) or a .txt."""
    name = path.stem
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            txts = [n for n in z.namelist() if n.lower().endswith(".txt") and not n.startswith("__MACOSX")]
            if not txts:
                raise ValueError("That zip has no chat in it (no .txt). Export the chat again from WhatsApp.")
            pick = next((n for n in txts if Path(n).name == "_chat.txt"), txts[0])
            if z.getinfo(pick).file_size > 200 * 1024 * 1024:
                raise ValueError("That chat is too big to import.")
            text = z.read(pick).decode("utf-8", errors="replace")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    name = re.sub(r"^WhatsApp Chat\s*(?:with|-)\s*", "", name, flags=re.I).strip() or "Imported chat"
    return text, name


def parse_export(text: str) -> list[tuple[float, str, str]]:
    """(time, sender, text) for each message; lines that don't start a message belong to the one before."""
    lines = text.translate(INVISIBLE).splitlines()
    heads = [(i, m) for i, line in enumerate(lines) if (m := LINE.match(line.strip()))]
    a = [int(m.group(1)) for _, m in heads]
    b = [int(m.group(2)) for _, m in heads]
    year_first = any(x > 31 for x in a)
    day_first = not year_first and (any(x > 12 for x in a) or not any(x > 12 for x in b) and _day_first_locale())
    out: list[tuple[float, str, str]] = []
    starts = {i: m for i, m in heads}
    current = None
    for i, line in enumerate(lines):
        m = starts.get(i)
        if m:
            x, y, z = int(m.group(1)), int(m.group(2)), int(m.group(3))
            year, month, day = (x, y, z) if year_first else (z, y, x) if day_first else (z, x, y)
            year += 2000 if year < 100 else 0
            hour, minute, sec = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
            ampm = (m.group(7) or "").replace(".", "").replace(" ", "").lower()
            if ampm:
                hour = hour % 12 + (12 if ampm == "pm" else 0)
            try:
                when = datetime(year, month, day, hour, minute, sec).timestamp()
            except ValueError:
                current = None
                continue
            rest = m.group(8).strip()
            if ": " not in rest:
                current = None  # "Messages are end-to-end encrypted", "Sam joined": not messages
                continue
            sender, said = rest.split(": ", 1)
            current = [when, sender.strip(), said]
            out.append(current)  # type: ignore[arg-type]
        elif current is not None:
            current[2] += "\n" + line
    return [(w, s, t.strip()) for w, s, t in out]


def _day_first_locale() -> bool:
    return not (os.environ.get("LANG", "") or "en_GB").startswith("en_US")


def import_export(home: Path, path: Path, chat: str | None = None) -> tuple[str, int]:
    """One exported chat into Ken's store. Importing the same file again adds nothing. Returns (chat, added)."""
    text, name = _read_export(Path(path))
    chat = (chat or name).strip()
    messages = parse_export(text)
    if not messages:
        raise ValueError("No messages found in that file. Is it a WhatsApp chat export?")
    seen: dict[str, int] = {}
    rows = []
    for when, sender, said in messages:
        base = hashlib.sha256(f"{chat}\0{when}\0{sender}\0{said}".encode()).hexdigest()[:32]
        seen[base] = seen.get(base, 0) + 1  # the same message twice in a row is still two messages
        rows.append(("export", f"{base}-{seen[base]}", chat, sender, 0, when, said))
    store = open_store(home)
    try:
        before = store.total_changes
        store.executemany("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?)", rows)
        store.commit()
        return chat, store.total_changes - before
    finally:
        store.close()


# ---------------------------------------------------------------------- reading it back (Ken's tools)

def _row(r) -> dict:
    return {"chat": r[0], "sender": r[1], "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[2])), "text": r[3]}


def chats(home: Path, limit: int = 50) -> list[dict]:
    store = open_store(home)
    try:
        return [{"chat": c, "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(t)), "messages": n} for c, t, n in store.execute(
            "SELECT chat, MAX(ts), COUNT(*) FROM messages GROUP BY chat ORDER BY MAX(ts) DESC LIMIT ?", (limit,))]
    finally:
        store.close()


def search(home: Path, words: str = "", chat: str = "", days: int = 0, limit: int = 50) -> list[dict]:
    """Messages with all these words (any order), newest first; in one chat, or the last few days, if asked."""
    where, args = [], []
    for w in words.split():
        where.append("text LIKE ? ESCAPE '\\'")
        args.append("%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if chat:
        where.append("chat LIKE ?")
        args.append(f"%{chat}%")
    if days:
        where.append("ts >= ?")
        args.append(time.time() - days * 86400)
    store = open_store(home)
    try:
        rows = store.execute("SELECT chat, sender, ts, text FROM messages" + (" WHERE " + " AND ".join(where) if where else "")
                             + " ORDER BY ts DESC LIMIT ?", (*args, max(1, min(int(limit), 200)))).fetchall()
        return [_row(r) for r in rows]
    finally:
        store.close()


def read(home: Path, chat: str, count: int = 40) -> list[dict]:
    """The last messages in one chat, oldest first."""
    return list(reversed(search(home, chat=chat, limit=count)))


def synced_at(home: Path) -> float | None:
    if not (home / "whatsapp.db").exists():
        return None
    store = open_store(home)
    try:
        row = store.execute("SELECT v FROM kv WHERE k='desktop_synced'").fetchone()
        return float(row[0]) if row else None
    finally:
        store.close()


# ---------------------------------------------------------------------- `ken --whatsapp …`

USAGE = """ken --whatsapp chats                                   their chats, most recent first
ken --whatsapp search [words…] [--chat <name>] [--days N]  messages with all these words, newest first
ken --whatsapp read <chat> [--count N]                    the last messages in one chat
ken --whatsapp import <file.zip|file.txt> [--chat <name>]  a WhatsApp "Export chat" file, for older history"""


def _opt(args: list[str], name: str, default=""):
    if name in args:
        i = args.index(name)
        value = args[i + 1] if i + 1 < len(args) else default
        del args[i:i + 2]
        return value
    return default


def _print(rows: list[dict]) -> None:
    for r in rows:
        print(f"[{r['time']}] {r['chat']} · {r['sender']}: {r['text']}")


def main(args: list[str], home: Path) -> int:
    """On any Ken: its own store (imports, and on a Mac what it synced). In the cloud: their Mac's too, when linked."""
    if not args:
        print(USAGE)
        return 2
    what, rest = args[0], list(args[1:])
    if what == "import" and rest:
        chat = _opt(rest, "--chat") or None
        try:
            name, added = import_export(home, Path(" ".join(rest)).expanduser(), chat)
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            print(str(e) if isinstance(e, ValueError) else f"Couldn't read that file: {e}")
            return 1
        print(f"Imported {added} new message(s) from “{name}”." if added else f"“{name}” was already imported; nothing new.")
        return 0
    if what == "chats":
        rows, mac_rows, cap = chats(home), None, ("whatsapp.chats", {})
    elif what == "search":
        chat, days = _opt(rest, "--chat"), int(_opt(rest, "--days", "0") or 0)
        q = {"words": " ".join(rest), "chat": chat, "days": days}
        rows, mac_rows, cap = search(home, **q), None, ("whatsapp.search", q)
    elif what == "read" and rest:
        count = int(_opt(rest, "--count", "40") or 40)
        q = {"chat": " ".join(rest), "count": count}
        rows, mac_rows, cap = read(home, q["chat"], count), None, ("whatsapp.read", q)
    else:
        print(USAGE)
        return 2
    import mac_link
    if mac_link._site():  # in the cloud: what their Mac has, too
        try:
            mac_rows = mac_link.ask_mac(*cap).get("messages" if what != "chats" else "chats", [])
        except mac_link.Refused as e:
            print(f"(Their Mac: {e})")
    if what == "chats":
        merged = {r["chat"]: r for r in (rows + (mac_rows or []))}
        for r in sorted(merged.values(), key=lambda r: r["last"], reverse=True):
            print(f"{r['chat']} · last {r['last']} · {r['messages']} messages")
        if not merged:
            print("No WhatsApp messages yet.")
        return 0
    both = {(r["chat"], r["time"], r["sender"], r["text"]): r for r in rows + (mac_rows or [])}
    out = sorted(both.values(), key=lambda r: r["time"], reverse=(what == "search"))
    if what == "read":
        out = out[-int(cap[1]["count"]):]
    _print(out)
    if not out:
        print("No messages found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], Path(os.environ.get("KEN_HOME", str(Path.home() / ".ken")))))
