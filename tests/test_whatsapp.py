"""Their WhatsApp, read-only (whatsapp.py, mac_link.py): WhatsApp Desktop's database read from a copy (with its -wal),
new messages only, Full Disk Access explained instead of failing silently, and "Export chat" files imported once.

Every database here is a fixture with WhatsApp's layout. The real one on this Mac is never opened: whatsapp.desktop_db
fails the test if anything asks for it."""
import os
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mac_link  # noqa: E402
import whatsapp  # noqa: E402

EPOCH = whatsapp.CORE_DATA_EPOCH


@pytest.fixture(autouse=True)
def never_the_real_whatsapp(monkeypatch):
    def nope():
        raise AssertionError("a test asked for the real WhatsApp database")
    monkeypatch.setattr(whatsapp, "desktop_db", nope)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def make_chatstorage(path: Path) -> sqlite3.Connection:
    """WhatsApp Desktop's ChatStorage.sqlite, the parts Ken reads (Core Data tables, Z_PK keys, 2001-based dates)."""
    db = sqlite3.connect(path)
    db.executescript("""
    PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
    CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID VARCHAR, ZPARTNERNAME VARCHAR, ZSESSIONTYPE INTEGER);
    CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZMEMBERJID VARCHAR, ZCONTACTNAME VARCHAR);
    CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZGROUPMEMBER INTEGER, ZISFROMME INTEGER,
      ZMESSAGEDATE TIMESTAMP, ZMESSAGETYPE INTEGER, ZTEXT VARCHAR, ZFROMJID VARCHAR, ZTOJID VARCHAR);
    INSERT INTO ZWACHATSESSION VALUES (1, '447700900123@s.whatsapp.net', 'Mum', 0), (2, '1203630@g.us', 'Lisbon trip', 1);
    INSERT INTO ZWAGROUPMEMBER VALUES (1, '447700900999@s.whatsapp.net', 'Ana');
    """)
    t = datetime(2026, 11, 20, 18, 0).timestamp() - EPOCH
    db.executemany("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?,?,?,?)", [
        (1, 1, None, 0, t, 0, "Are you coming for dinner on Sunday?", "447700900123@s.whatsapp.net", None),
        (2, 1, None, 1, t + 60, 0, "Yes! I'll bring dessert", None, "447700900123@s.whatsapp.net"),
        (3, 2, 1, 0, t + 120, 0, "Flights are booked: TP1351 on the 4th", "1203630@g.us", None),
        (4, 2, 1, 0, t + 180, 1, None, "1203630@g.us", None),  # a photo
        (5, 2, None, 0, t + 190, 6, None, "1203630@g.us", None),  # "Ana added you": not a message
    ])
    db.commit()
    return db


def test_new_messages_come_from_a_copy_with_names_dates_and_who_said_it(home, tmp_path, monkeypatch):
    src = tmp_path / "wa" / "ChatStorage.sqlite"
    src.parent.mkdir()
    wa = make_chatstorage(src)
    assert whatsapp.sync_desktop(home, src) == 4
    got = whatsapp.search(home, limit=10)
    assert [(m["chat"], m["sender"], m["text"]) for m in got] == [
        ("Lisbon trip", "Ana", "[photo]"),
        ("Lisbon trip", "Ana", "Flights are booked: TP1351 on the 4th"),
        ("Mum", "me", "Yes! I'll bring dessert"),
        ("Mum", "Mum", "Are you coming for dinner on Sunday?")]
    assert got[-1]["time"] == "2026-11-20 18:00"
    # Only what's new next time (by Z_PK), and nothing twice.
    wa.execute("INSERT INTO ZWAMESSAGE VALUES (6, 1, NULL, 0, ?, 0, 'Bring the good plates', NULL, NULL)", (time.time() - EPOCH,))
    wa.commit()
    assert whatsapp.sync_desktop(home, src) == 1
    copies = []
    monkeypatch.setattr(whatsapp, "_copy", lambda *a: copies.append(a))
    assert whatsapp.sync_desktop(home, src) == 0 and copies == []  # nothing changed: not even copied
    monkeypatch.undo()
    monkeypatch.setattr(whatsapp, "desktop_db", lambda: (_ for _ in ()).throw(AssertionError("real WhatsApp")))
    assert whatsapp.search(home, "plates")[0]["chat"] == "Mum"
    assert [c["chat"] for c in whatsapp.chats(home)] == ["Mum", "Lisbon trip"]
    wa.close()


def test_the_newest_messages_still_in_the_wal_are_read(home, tmp_path):
    src = tmp_path / "ChatStorage.sqlite"
    wa = make_chatstorage(src)  # still open, autocheckpoint off: every row is only in the -wal
    assert Path(str(src) + "-wal").stat().st_size > 0
    alone = tmp_path / "alone"
    alone.mkdir()
    (alone / "ChatStorage.sqlite").write_bytes(src.read_bytes())
    with pytest.raises(sqlite3.OperationalError):  # the main file by itself doesn't even have the tables yet
        sqlite3.connect(alone / "ChatStorage.sqlite").execute("SELECT * FROM ZWAMESSAGE").fetchall()
    assert whatsapp.sync_desktop(home, src) == 4
    wa.close()


def test_the_original_is_never_written(home, tmp_path):
    src = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(src).close()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    whatsapp.sync_desktop(home, src)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before


# ------------------------------------------------------------------ on the Mac: the switch, and Full Disk Access

def mac(home, db):
    link = mac_link.MacLink(home, site="https://site.test", name=lambda: "Mac", whatsapp_db=lambda: db)
    link._save({**link.state(), "token": "t" * 64, "account": "sam@example.com"})  # linked
    return link


def test_unlinked_means_no_reading_even_if_the_switch_was_on(home, tmp_path):
    src = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(src).close()
    link = mac(home, src)
    link.set_switch("whatsapp", True)
    link._forget("")  # unlinked (which also turns the switch off) …
    st = link.state()  # … and even with the switch somehow still on
    st["switches"]["whatsapp"] = True
    link._save(st)
    assert link.sync_whatsapp() is None and whatsapp.synced_at(home) is None


def test_denied_when_opening_the_file_is_full_disk_access_not_missing(home, tmp_path, monkeypatch):
    src = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(src).close()
    real_open = open

    def guarded(path, *a, **k):
        if str(path).startswith(str(tmp_path / "ChatStorage.sqlite")):
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", guarded)
    with pytest.raises(whatsapp.NeedsAccess):
        whatsapp.sync_desktop(home, src)


async def test_off_means_whatsapp_is_never_looked_at(home):
    def looked():
        raise AssertionError("looked at WhatsApp with the switch off")
    link = mac_link.MacLink(home, site="https://site.test", whatsapp_db=looked)
    assert link.sync_whatsapp() is None
    r = await link.handle({"cap": "whatsapp.search", "args": {"words": "dinner"}})
    assert r["ok"] is False and "WhatsApp (read)" in r["error"] and "is off" in r["error"]


async def test_without_full_disk_access_they_are_told_and_shown_where(home, tmp_path, monkeypatch):
    src = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(src).close()
    link = mac(home, src)
    link.set_switch("whatsapp", True)
    monkeypatch.setattr(whatsapp.shutil, "copyfile", lambda *a: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted")))
    assert link.sync_whatsapp() is None
    assert link.status()["whatsapp"]["problem"] == "access"
    r = await link.handle({"cap": "whatsapp.search", "args": {"words": "dinner"}})
    assert r["ok"] is False and "Full Disk Access" in r["error"]
    opened = []
    monkeypatch.setattr(mac_link.subprocess, "Popen", lambda args, **k: opened.append(args))
    monkeypatch.setattr(mac_link.sys, "platform", "darwin")
    link.open_full_disk_access()
    assert opened == [["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"]]


async def test_no_whatsapp_on_this_mac(home, tmp_path):
    link = mac(home, tmp_path / "nothing" / "ChatStorage.sqlite")
    link.set_switch("whatsapp", True)
    assert link.sync_whatsapp() is None and link.whatsapp_problem == "missing"
    assert "isn't on their Mac" in (await link.handle({"cap": "whatsapp.chats"}))["error"]


async def test_the_cloud_ken_searches_and_reads_through_the_link(home, tmp_path):
    src = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(src).close()
    link = mac(home, src)
    link.set_switch("whatsapp", True)
    assert link.sync_whatsapp() == 4 and link.status()["whatsapp"]["synced"]
    found = (await link.handle({"cap": "whatsapp.search", "args": {"words": "flights booked"}}))["result"]["messages"]
    assert [m["sender"] for m in found] == ["Ana"]
    read = (await link.handle({"cap": "whatsapp.read", "args": {"chat": "mum", "count": 5}}))["result"]["messages"]
    assert [m["sender"] for m in read] == ["Mum", "me"]  # oldest first
    chats = (await link.handle({"cap": "whatsapp.chats"}))["result"]["chats"]
    assert {c["chat"] for c in chats} == {"Mum", "Lisbon trip"}


async def test_the_switch_turning_on_reads_at_once(home, tmp_path, monkeypatch):
    import asyncio
    src = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(src).close()
    link = mac(home, src)
    task = asyncio.create_task(link.keep_whatsapp())
    await asyncio.sleep(0.05)
    assert whatsapp.synced_at(home) is None
    link.set_switch("whatsapp", True)
    real_sleep = asyncio.sleep
    for _ in range(100):
        await real_sleep(0.02)
        if whatsapp.synced_at(home):
            break
    task.cancel()
    assert whatsapp.synced_at(home)


# ------------------------------------------------------------------ "Export chat" files, on any Ken

IPHONE = ("‎[03/11/2026, 9:41:05 PM] Sam: Messages and calls are end-to-end encrypted.\n"
          "[03/11/2026, 9:41:05 PM] Sam: The keys are under the mat\n"
          "and the code is 4412\n"
          "[13/11/2026, 7:02:44 AM] Prasann: thanks!\n"
          "[13/11/2026, 7:02:44 AM] Prasann: thanks!\n"
          "‎[13/11/2026, 7:03:00 AM] Sam changed the group name\n")
ANDROID = ("12/31/25, 11:58 PM - Messages and calls are end-to-end encrypted.\n"
           "12/31/25, 11:59 PM - Ana: Happy new year!!\n"
           "1/1/26, 12:01 AM - Prasann: you too\n")


def test_an_iphone_export_zip_imports_once(home, tmp_path):
    z = tmp_path / "WhatsApp Chat - Sam.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("_chat.txt", IPHONE)
        f.writestr("00000012-PHOTO-2026-11-03.jpg", b"\xff\xd8")
    assert whatsapp.import_export(home, z) == ("Sam", 4)  # a repeated line is still two messages
    assert whatsapp.import_export(home, z) == ("Sam", 0)
    msgs = whatsapp.read(home, "Sam")
    assert msgs[1]["text"] == "The keys are under the mat\nand the code is 4412"
    assert msgs[1]["time"] == "2026-11-03 21:41"
    assert [m["sender"] for m in msgs] == ["Sam", "Sam", "Prasann", "Prasann"]
    assert not any("changed the group name" in m["text"] for m in msgs)


def test_an_android_export_txt_month_first(home, tmp_path):
    t = tmp_path / "WhatsApp Chat with Ana.txt"
    t.write_text(ANDROID)
    assert whatsapp.import_export(home, t) == ("Ana", 2)
    msgs = whatsapp.read(home, "Ana")
    assert [(m["time"], m["sender"]) for m in msgs] == [("2025-12-31 23:59", "Ana"), ("2026-01-01 00:01", "Prasann")]


def test_not_a_chat(home, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("just some notes\n")
    with pytest.raises(ValueError, match="No messages"):
        whatsapp.import_export(home, bad)
    empty = tmp_path / "x.zip"
    with zipfile.ZipFile(empty, "w") as f:
        f.writestr("photo.jpg", b"1")
    with pytest.raises(ValueError, match="no chat"):
        whatsapp.import_export(home, empty)


def test_ken_command_line_merges_the_macs_messages_in_the_cloud(home, tmp_path, monkeypatch, capsys):
    t = tmp_path / "WhatsApp Chat with Ana.txt"
    t.write_text(ANDROID)
    assert whatsapp.main(["import", str(t)], home) == 0
    assert "Imported 2 new message(s)" in capsys.readouterr().out
    monkeypatch.setattr(mac_link, "_site", lambda: ("https://site.test/box/mac/call", "tok"))
    asked = []

    def ask(cap, args=None, timeout=60):
        asked.append((cap, args))
        return {"messages": [{"chat": "Mum", "sender": "Mum", "time": "2026-11-20 18:00", "text": "Happy new year from Mum"}]}
    monkeypatch.setattr(mac_link, "ask_mac", ask)
    assert whatsapp.main(["search", "happy", "new", "--days", "0"], home) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("[2026-11-20 18:00] Mum · Mum") and "Ana · Ana: Happy new year!!" in out[1]
    assert asked == [("whatsapp.search", {"words": "happy new", "chat": "", "days": 0})]
    monkeypatch.setattr(mac_link, "ask_mac", lambda *a, **k: (_ for _ in ()).throw(mac_link.Refused("Sam's Mac is off.")))
    assert whatsapp.main(["read", "Ana"], home) == 0
    out = capsys.readouterr().out
    assert "(Their Mac: Sam's Mac is off.)" in out and "you too" in out


def test_the_cloud_ken_is_told_about_whatsapp_only_when_its_switched_on(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "LINKED_MAC", {"name": "Sam's Mac", "caps": ["files"]})
    assert "--whatsapp search" not in bot.mac_prompt() and "WhatsApp (read)" in bot.mac_prompt()
    monkeypatch.setattr(bot, "LINKED_MAC", {"name": "Sam's Mac", "caps": ["whatsapp"]})
    assert "--whatsapp search" in bot.mac_prompt() and "can't send" in bot.mac_prompt()
    assert "--whatsapp import" in bot.whatsapp_prompt()

