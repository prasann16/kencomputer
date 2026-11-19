"""No Telegram linked: nothing empties the outbox, so its files are kept elsewhere at start (bot.tidy_outbox), never
deleted, and an old message's outbox path still opens them (engine.shared_file). Ken only uses the outbox for Telegram."""
import os
from pathlib import Path

import bot


def setup(monkeypatch, tmp_path):
    home = tmp_path / ".ken"
    monkeypatch.setattr(bot, "OUTBOX_DIR", home / "outbox")
    monkeypatch.setattr(bot, "SENT_DIR", home / "sent")
    monkeypatch.setattr(bot, "INBOX_DIR", home / "inbox")
    work = home / "work"
    for d in ("outbox", "sent", "inbox", "work/videos/reel"):
        (home / d).mkdir(parents=True, exist_ok=True)
    return home, work


def test_copies_become_links_to_their_original_and_the_rest_moves_never_deleted(monkeypatch, tmp_path):
    home, work = setup(monkeypatch, tmp_path)
    original = work / "videos" / "reel" / "reel.mp4"
    original.write_bytes(b"R" * 5000)
    (home / "outbox" / "my-reel.mp4").write_bytes(b"R" * 5000)  # a copy of the original
    (home / "outbox" / "plan.pdf").write_bytes(b"only here")  # no other copy anywhere
    (home / "outbox" / "same-size.pdf").write_bytes(b"X" * 5000)  # same size as the reel, different bytes
    out = bot.tidy_outbox(work)
    assert out == {"linked": 1, "moved": 2}
    assert not any((home / "outbox").iterdir())
    kept = home / "sent" / "my-reel.mp4"
    assert kept.read_bytes() == b"R" * 5000 and os.stat(kept).st_ino == os.stat(original).st_ino  # one file on disk
    assert (home / "sent" / "plan.pdf").read_bytes() == b"only here"
    assert (home / "sent" / "same-size.pdf").read_bytes() == b"X" * 5000
    assert original.read_bytes() == b"R" * 5000


def test_a_different_file_already_kept_by_that_name_is_never_overwritten(monkeypatch, tmp_path):
    home, work = setup(monkeypatch, tmp_path)
    (home / "sent" / "a.pdf").write_bytes(b"older")
    (home / "outbox" / "a.pdf").write_bytes(b"newer")
    bot.tidy_outbox(work)
    assert (home / "sent" / "a.pdf").read_bytes() == b"older" and (home / "outbox" / "a.pdf").read_bytes() == b"newer"


def test_ken_only_hears_about_the_outbox_where_telegram_is_linked(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)  # the video lines show only with ffmpeg
    facts = dict(bot.where_facts(), kind="cloud", app=True, own=False, keep_awake=False, mac_app=False, voice=False, browser="")
    without = bot.where_you_are(dict(facts, telegram=False))
    with_tg = bot.where_you_are(dict(facts, telegram=True))
    assert "outbox" not in without.lower() and str(bot.OUTBOX_DIR) in with_tg
    assert "never mention it to them" in with_tg
    assert "outbox" not in bot.WORK_RULES.lower() and "outbox" not in bot.video_prompt().lower()


def test_an_old_outbox_path_opens_the_kept_file(tmp_path):
    from test_engine import FakeBrain
    from engine import Engine

    e = Engine(tmp_path, FakeBrain())
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    (tmp_path / "outbox").mkdir()
    (tmp_path / "sent").mkdir()
    (tmp_path / "sent" / "plan.pdf").write_bytes(b"pdf")
    assert e.shared_file(str(tmp_path / "outbox" / "plan.pdf")) == (tmp_path / "sent" / "plan.pdf").resolve()
    (tmp_path / "outbox" / "new.pdf").write_bytes(b"new")  # still in the outbox: that one
    assert e.shared_file(str(tmp_path / "outbox" / "new.pdf")) == (tmp_path / "outbox" / "new.pdf").resolve()
