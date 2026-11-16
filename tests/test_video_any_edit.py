"""Not only reels: any finished video hosted Ken gives them (a quick ffmpeg trim, a title) is kept in their Drive or on
their computer. `save <file>` takes one video, and Ken's own check after each reply has Ken save one the reply left here
(Swap's VM test, Nov 16: a 15 s trim went to the outbox and stayed on the computer)."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drive_store  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401
from test_video_drive import home, hosted_ken  # noqa: E402,F401


@pytest.fixture
def trim(home, monkeypatch):
    """What Ken made in the VM test: the trim in a work folder, a copy in the outbox, the original in the inbox."""
    monkeypatch.setattr(drive_store, "MIN_BYTES", 1000)
    monkeypatch.setattr(drive_store, "_nudged", set())
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    data = os.urandom(5000)
    work = home / "work" / "drive-launch" / "launch-15s.mp4"
    work.parent.mkdir(parents=True)
    work.write_bytes(data)
    (home / "outbox").mkdir()
    (home / "outbox" / "launch-15s.mp4").write_bytes(data)
    (home / "inbox").mkdir()
    (home / "inbox" / "launch.mp4").write_bytes(os.urandom(8000))
    return work


REPLY = ("The 15-second clip is done.\n- `{home}/outbox/launch-15s.mp4` (sent to your phone)\n"
         "- `{work}` (the file you can open in the app)\nThe original is untouched in `{home}/inbox/launch.mp4`.")


def test_save_takes_a_single_video_file(home, trim, monkeypatch):
    monkeypatch.setenv("KEN_DRIVE_FAKE", str(home / "drive"))
    monkeypatch.setattr(drive_store, "upload", lambda path, account="", parent="": {
        "id": "F1", "name": path.name, "link": "https://drive.google.com/file/d/F1/view",
        "size": path.stat().st_size, "md5": drive_store.md5(path)})
    said = []
    assert drive_store.save(trim, True, "", said.append) == 0
    assert not trim.exists() and "https://drive.google.com/file/d/F1/view" in said[0]
    assert not (home / "outbox" / "launch-15s.mp4").exists()  # no phone to deliver it: its outbox copy goes too
    assert (home / "inbox" / "launch.mp4").exists()  # what they sent stays
    assert drive_store.save(home / "work" / "drive-launch" / "notes.txt", True, "", said.append) == 1


def test_with_a_phone_the_outbox_copy_is_left_for_telegram(home, trim, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(drive_store, "upload", lambda path, account="", parent="": {
        "id": "F1", "name": path.name, "link": "L", "size": path.stat().st_size, "md5": drive_store.md5(path)})
    monkeypatch.setattr(drive_store, "folder_id", lambda account="": "fld")
    assert drive_store.save(trim, True, "", lambda s: None) == 0
    assert (home / "outbox" / "launch-15s.mp4").exists()


def test_a_single_file_can_be_offered_on_their_computer(home, trim, monkeypatch):
    monkeypatch.setattr(drive_store, "_web", lambda path, body=None, timeout=5: {})
    said = []
    assert drive_store.save_to_computer(trim, True, said.append) == 0 and "tap Save" in said[0]
    (p,) = drive_store.pickups()
    assert p["kind"] == "video" and p["name"] == "launch-15s.mp4" and p["label"] == "launch-15s"
    done = drive_store.picked_up(p["token"], p["size"], p["sha256"])
    assert done["ok"] and not trim.exists() and not (home / "outbox" / "launch-15s.mp4").exists()


def test_after_a_reply_a_video_left_here_gets_saved_once(home, trim, monkeypatch):
    monkeypatch.setenv("KEN_DRIVE_FAKE", str(home / "drive"))  # active: switched on and a Drive to reach
    note = drive_store.after_turn([REPLY.format(home=home, work=trim)], "ken")
    # the work copy, not the outbox one (Telegram's delivery deletes that) and never the original they sent
    assert note and f"`ken --video-drive save {trim.resolve()}`" in note and "inbox" not in note and "outbox" not in note
    assert drive_store.after_turn([REPLY.format(home=home, work=trim)], "ken") is None  # asked once, not in a loop


def test_a_video_only_in_the_outbox_gets_a_copy_of_its_own_to_keep(home, trim, monkeypatch):
    monkeypatch.setenv("KEN_DRIVE_FAKE", str(home / "drive"))
    trim.unlink()
    note = drive_store.after_turn(["Here it is, on its way to your phone."], "ken")
    keep = home / "work" / "videos" / "launch-15s" / "launch-15s.mp4"
    assert keep.read_bytes() == (home / "outbox" / "launch-15s.mp4").read_bytes()
    assert f"save {keep.resolve()}`" in note


def test_a_reel_is_saved_by_its_project_folder(home, trim, monkeypatch):
    monkeypatch.setenv("KEN_DRIVE_FAKE", str(home / "drive"))
    folder = home / "work" / "videos" / "talk"
    folder.mkdir(parents=True)
    (folder / "reel.mp4").write_bytes(os.urandom(4000))
    (folder / "source.mov").write_bytes(os.urandom(4000))
    (folder / "cut.mp4").write_bytes(os.urandom(4000))
    trim.unlink()
    (home / "outbox" / "launch-15s.mp4").unlink()
    note = drive_store.after_turn([f"Done: `{folder}/reel.mp4` (working files `{folder}/cut.mp4`, `{folder}/source.mov`)"], "ken")
    assert f"save {folder.resolve()}`" in note and "cut.mp4" not in note and "source" not in note


def test_small_files_and_ones_waiting_on_their_computer_are_left_alone(home, trim, monkeypatch):
    monkeypatch.setenv("KEN_DRIVE_FAKE", str(home / "drive"))
    monkeypatch.setattr(drive_store, "MIN_BYTES", 10_000)
    assert drive_store.after_turn([REPLY.format(home=home, work=trim)], "ken") is None
    monkeypatch.setattr(drive_store, "MIN_BYTES", 1000)
    monkeypatch.setattr(drive_store, "_web", lambda path, body=None, timeout=5: {})
    drive_store.save_to_computer(trim, True, lambda s: None)
    (home / "outbox" / "launch-15s.mp4").unlink()
    assert drive_store.after_turn([REPLY.format(home=home, work=trim)], "ken") is None


def test_switched_off_or_not_in_the_cloud_there_is_no_check(home, trim, monkeypatch):
    monkeypatch.delenv("KEN_DRIVE_FAKE", raising=False)
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)
    assert drive_store.after_turn([REPLY.format(home=home, work=trim)], "ken") is None  # a Ken on its own computer
    monkeypatch.setenv("KEN_DRIVE_FAKE", str(home / "drive"))
    monkeypatch.delenv("KEN_VIDEO_DRIVE")
    assert drive_store.after_turn([REPLY.format(home=home, work=trim)], "ken") is None


async def test_the_engine_runs_one_more_turn_with_the_note(engine):  # noqa: F811
    from engine import HOME
    seen = []

    def check(pid, said):
        seen.append((pid, list(said)))
        return "(Ken's own check: save it.)" if len(seen) == 1 else None
    engine.after_turn = check
    await engine.send_message(HOME, "trim my video")
    await asyncio.gather(*engine._bg)
    prompts = [a["prompt"] for a in engine.brain.asks]
    assert len(prompts) == 2 and "(Ken's own check: save it.)" in prompts[1]
    assert seen[0] == (HOME, ["did it"])


async def test_a_failing_check_never_breaks_the_reply(engine):  # noqa: F811
    from engine import HOME

    def boom(pid, said):
        raise RuntimeError("disk")
    engine.after_turn = boom
    assert await engine.send_message(HOME, "trim my video") is True
    assert len(engine.brain.asks) == 1


def test_the_drive_steps_come_after_the_video_steps_and_cover_quick_edits(monkeypatch):
    import bot
    hosted_ken(bot, monkeypatch)
    monkeypatch.setenv("KEN_VIDEO_DRIVE", "1")
    system = bot.build_system()
    assert system.index("--video-drive check") > system.index("VIDEO")
    assert "ffmpeg trim" in system and "<the video file, or a reel's project folder>" in system


def test_bot_check_is_off_without_the_switch(monkeypatch):
    import bot
    monkeypatch.delenv("KEN_VIDEO_DRIVE", raising=False)
    assert bot.video_storage_check("home", ["`/x/y.mp4`"]) is None
