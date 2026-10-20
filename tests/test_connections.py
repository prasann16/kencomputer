"""Connections without a network: calendars, email composition, tools and prompts."""
import json
import os
import time

import icalendar
import pytest

import connections
from connections import Calendars, Connections, Email

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:Work
BEGIN:VEVENT
UID:standup
DTSTART:20261005T140000Z
DTEND:20261005T141500Z
RRULE:FREQ=DAILY;COUNT=5
SUMMARY:Standup
END:VEVENT
BEGIN:VEVENT
UID:offsite
DTSTART;VALUE=DATE:20261007
DTEND;VALUE=DATE:20261008
SUMMARY:Offsite
LOCATION:Lisbon
END:VEVENT
END:VCALENDAR
"""


def calendars():
    cals = Calendars([{"name": "Work", "url": "https://example.com/work.ics"}])
    cals.cache["https://example.com/work.ics"] = (time.time(), icalendar.Calendar.from_ical(ICS))
    return cals


def test_calendar_expands_repeats_and_all_day_events():
    out = calendars().events("2026-10-07", 1)
    assert "Standup" in out and "Offsite" in out and "(all day)" in out and "Lisbon" in out
    assert calendars().events("2026-10-05", 7).count("Standup") == 5


def test_calendar_empty_day():
    assert calendars().events("2026-12-01", 1).startswith("Nothing on the calendar")


def test_reply_threads_and_quotes_subject(monkeypatch):
    original = connections.email.message_from_bytes(
        b"From: Ana <ana@example.com>\r\nSubject: Invoice\r\nMessage-ID: <m1@example.com>\r\n\r\nHi")

    class FakeIMAP:
        def select(self, *a, **k): pass
        def logout(self): pass

    mail = Email({"address": "me@example.com", "password": "x", "imap_host": "imap.example.com",
                  "smtp_host": "smtp.example.com", "smtp_port": 587})
    monkeypatch.setattr(mail, "imap", lambda: FakeIMAP())
    monkeypatch.setattr(mail, "_fetch", lambda m, uid: original)
    msg = mail.compose(to="", subject="", body="Paid, thanks.", reply_to_uid="7")
    assert msg["To"] == "Ana <ana@example.com>" and msg["Subject"] == "Re: Invoice"
    assert msg["In-Reply-To"] == "<m1@example.com>"


def test_known_servers_and_bad_login(tmp_path, monkeypatch):
    async def run():
        c = Connections(tmp_path)

        def refuse(self):
            raise connections.imaplib.IMAP4.error("auth")
        monkeypatch.setattr(Email, "imap", refuse)
        with pytest.raises(ValueError, match="didn't work"):
            await c.connect_email("me@gmail.com", "abcd efgh ijkl mnop")
        monkeypatch.setattr(Email, "imap", lambda self: type("M", (), {"logout": lambda s: None})())
        await c.connect_email("me@gmail.com", "abcd efgh ijkl mnop")
        saved = json.loads((tmp_path / "connections.json").read_text())["emails"][0]
        assert saved["imap_host"] == "imap.gmail.com" and saved["password"] == "abcdefghijklmnop"
        if os.name != "nt":  # Windows has no Unix file modes; the user folder is private there
            assert oct((tmp_path / "connections.json").stat().st_mode)[-3:] == "600"
    import asyncio
    asyncio.run(run())


def test_tools_and_prompt_follow_what_is_connected(tmp_path):
    c = Connections(tmp_path)
    assert c.mcp() == {} and c.prompt() == ""
    c.save({"emails": [{"address": "me@gmail.com", "password": "x", "imap_host": "imap.gmail.com",
                        "smtp_host": "smtp.gmail.com", "smtp_port": 465}],
            "calendars": [{"name": "Work", "url": "https://example.com/work.ics"}]})
    assert "ken" in c.mcp()
    assert "EMAIL (me@gmail.com)" in c.prompt() and "CALENDAR (Work)" in c.prompt()
    assert c.status()["emails"] == [{"address": "me@gmail.com"}] and "password" not in json.dumps(c.status())


def test_several_email_accounts(tmp_path, monkeypatch):
    async def run():
        c = Connections(tmp_path)
        # A file from before Ken took several accounts still loads.
        (tmp_path / "connections.json").write_text(json.dumps({"email": {"address": "old@gmail.com", "password": "x",
            "imap_host": "imap.gmail.com", "smtp_host": "smtp.gmail.com", "smtp_port": 465}}))
        assert c.status()["emails"] == [{"address": "old@gmail.com"}]
        monkeypatch.setattr(Email, "imap", lambda self: type("M", (), {"logout": lambda s: None})())
        await c.connect_email("work@icloud.com", "pw")
        await c.connect_email("old@gmail.com", "new-pw")  # reconnecting replaces, never duplicates
        assert [e["address"] for e in c.status()["emails"]] == ["work@icloud.com", "old@gmail.com"]
        assert "old@gmail.com, work@icloud.com" in c.prompt() or "work@icloud.com, old@gmail.com" in c.prompt()
        assert "need the `account`" in c.prompt()
        await c.disconnect_email("WORK@icloud.com")
        assert c.status()["emails"] == [{"address": "old@gmail.com"}]
        await c.disconnect_email("old@gmail.com")
        assert c.status()["emails"] == [] and c.mcp() == {}
    import asyncio
    asyncio.run(run())


def test_chrome_counts_as_connected_only_while_it_listens(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(connections, "chrome_profile", lambda: tmp_path)
    assert not connections.chrome_connected()  # no port file
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    (tmp_path / "DevToolsActivePort").write_text(f"{port}\n/devtools/browser/abc\n")
    assert connections.chrome_connected() and Connections(tmp_path).status()["chrome"]
    server.close()  # Chrome restarted with the setting off: the file stays behind
    assert not connections.chrome_connected()
