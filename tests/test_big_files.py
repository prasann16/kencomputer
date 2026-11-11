"""Hosted Ken: a file too big for Telegram to pass on (over 20 MB) gets a one-tap link at once, the web app takes the
file instead, and Ken carries on with what was asked, answering in Telegram. The same for the shared bot and own bots
(both reach Ken as pushed updates)."""
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from engine import HOME  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401

SITE = "https://app.example.dev"
MB = 1024 * 1024


EDITS = []


class Msg:
    def __init__(self, caption="make a reel of this", chat_id=777001, message_id=42):
        self.chat_id, self.message_id, self.caption, self.replies = chat_id, message_id, caption, []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return SimpleNamespace(message_id=900 + len(self.replies))

    def get_bot(self):
        class Bot:
            async def edit_message_text(self, text, chat_id, message_id):
                EDITS.append((chat_id, message_id, text))
        return Bot()


@pytest.fixture
def waiting(monkeypatch):
    monkeypatch.setattr(bot, "BIG_FILES", Path(tempfile.mkdtemp()) / "telegram-big-files.json")


@pytest.fixture
def hosted(monkeypatch, waiting):
    monkeypatch.setenv("KEN_KEEPALIVE_URL", f"{SITE}/box/checkin")


async def test_too_big_replies_at_once_with_a_link_and_keeps_what_they_asked(hosted):
    msg = Msg()
    assert await bot.too_big(msg, SimpleNamespace(file_size=180 * MB), "a video", "IMG_0042.MOV")
    [reply] = msg.replies
    token = re.fullmatch(rf"That video is too big for Telegram to pass on\. Send it here: {SITE}/send/([0-9a-f]{{24}})", reply).group(1)
    w = bot.waiting_big_file(token)
    assert w | {"ts": 0, "last": 0} == {"chat_id": 777001, "message_id": 42, "caption": "make a reel of this", "kind": "a video",
                                        "name": "IMG_0042.MOV", "size": 180 * MB, "ts": 0, "last": 0, "replied": True, "reply_id": 901,
                                        "files": [{"kind": "a video", "name": "IMG_0042.MOV", "size": 180 * MB}]}
    other = Msg("", chat_id=555)
    assert await bot.too_big(other, SimpleNamespace(file_size=30 * MB), "a file", "notes.pdf")
    assert other.replies[0].startswith("That file is too big for Telegram to pass on.")
    later = Msg("", chat_id=556)
    assert await bot.too_big(later, SimpleNamespace(file_size=40 * MB), "a recording", "call.m4a")
    assert later.replies[0].startswith("That recording is too big")


async def test_several_too_big_together_get_one_reply_that_grows(hosted, monkeypatch):
    """Six full-quality videos sent at once: one reply, one link, saying how many; a batch much later gets its own."""
    EDITS.clear()
    first = Msg("make a reel")
    await bot.too_big(first, SimpleNamespace(file_size=50 * MB), "a video", "IMG_7613.MOV")
    token = first.replies[0].rsplit("/", 1)[1]
    for i, name in enumerate(["IMG_7663.MOV", "IMG_7664.MOV"]):
        more = Msg("", message_id=43 + i)
        assert await bot.too_big(more, SimpleNamespace(file_size=60 * MB), "a video", name)
        assert more.replies == []  # no second message
    assert EDITS[-1] == (777001, 901, f"Those 3 videos are too big for Telegram to pass on. Send them all here: {SITE}/send/{token}")
    assert [f["name"] for f in bot.waiting_big_file(token)["files"]] == ["IMG_7613.MOV", "IMG_7663.MOV", "IMG_7664.MOV"]
    assert bot.waiting_big_file(token)["caption"] == "make a reel"
    now = bot.time.time()
    monkeypatch.setattr(bot.time, "time", lambda: now + bot.BIG_FILE_GROUP_S + 1)
    late = Msg("")
    await bot.too_big(late, SimpleNamespace(file_size=50 * MB), "a video", "IMG_9000.MOV")
    assert late.replies and late.replies[0].rsplit("/", 1)[1] != token


async def test_small_files_go_through_and_ken_on_its_own_computer_says_what_it_always_did(waiting, monkeypatch):
    msg = Msg()
    assert not await bot.too_big(msg, SimpleNamespace(file_size=19 * MB), "a video", "a.mp4")
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)
    assert await bot.too_big(msg, SimpleNamespace(file_size=50 * MB), "a video", "a.mp4")
    assert msg.replies == ["That's 50 MB, and Telegram only lets me download files up to 20 MB. Send me a link to it "
                           "instead (iCloud, Google Drive or Dropbox), or drop it into the Ken app on your computer."]


async def test_links_expire_after_a_day(hosted, monkeypatch):
    msg = Msg()
    await bot.too_big(msg, SimpleNamespace(file_size=50 * MB), "a video", "a.mp4")
    token = msg.replies[0].rsplit("/", 1)[1]
    now = bot.time.time()
    monkeypatch.setattr(bot.time, "time", lambda: now + 86401)
    assert bot.waiting_big_file(token) is None and bot.take_big_file(token) is None
    assert bot.waiting_big_file("../../etc") is None


async def test_when_it_arrives_ken_carries_on_and_answers_in_telegram(hosted, monkeypatch, tmp_path):
    msg = Msg()
    await bot.too_big(msg, SimpleNamespace(file_size=180 * MB), "a video", "IMG_0042.MOV")
    token = msg.replies[0].rsplit("/", 1)[1]
    sent, asked = [], {}

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))

    async def send_message(pid, text, files=None, **kw):
        asked.update(pid=pid, text=text, files=files, **kw)
        await kw["listener"]("Your reel is ready.")
        return True

    monkeypatch.setattr(bot, "ENGINE", SimpleNamespace(_bg=[], send_message=send_message))
    monkeypatch.setattr(bot, "log_history", lambda *a: None)
    monkeypatch.setattr(bot, "flush_outbox", lambda *a, **k: asyncio.sleep(0))
    phone = bot.Phone()
    phone.app = SimpleNamespace(bot=FakeBot())
    assert phone.waiting_file(token) == {"kind": "a video", "name": "IMG_0042.MOV", "caption": "make a reel of this", "size": 180 * MB,
                                         "files": [{"kind": "a video", "name": "IMG_0042.MOV", "size": 180 * MB}], "asked": False}
    path = tmp_path / "IMG_0042.MOV"
    assert await phone.file_arrived(token, path)
    await asyncio.gather(*bot.ENGINE._bg)
    assert asked["pid"] == HOME and asked["text"] == "make a reel of this" and asked["files"] == ["IMG_0042.MOV"]
    assert asked["via"] == "telegram" and f"saved at {path}" in asked["prompt"] and 'Their message with it: "make a reel of this"' in asked["prompt"]
    assert sent == [(777001, "Your reel is ready.")]
    assert not await phone.file_arrived(token, path)  # used once
    assert phone.waiting_file(token) is None


async def test_the_web_app_endpoints(engine, aiohttp_client, hosted):  # noqa: F811
    calls = []

    class FakePhone:
        def waiting_file(self, token):
            return {"kind": "a video", "name": "IMG_0042.MOV", "caption": "", "size": 1} if token == "a" * 24 else None

        async def file_arrived(self, token, path):
            calls.append((token, path))
            return token == "a" * 24

    app, client = await signed_in(engine, aiohttp_client, phone=FakePhone())
    assert (await (await client.get(f"/api/telegram-files/{'a' * 24}")).json())["name"] == "IMG_0042.MOV"
    gone = await client.get(f"/api/telegram-files/{'b' * 24}")
    assert gone.status == 404 and "expired" in (await gone.json())["error"]
    assert (await client.post(f"/api/telegram-files/{'a' * 24}", json={"file": "missing.mov"})).status == 400
    assert (await client.post(f"/api/telegram-files/{'a' * 24}", json={"file": "../secrets"})).status == 400
    engine.files_dir(HOME).mkdir(parents=True, exist_ok=True)
    (engine.files_dir(HOME) / "IMG_0042.MOV").write_bytes(b"video")
    r = await client.post(f"/api/telegram-files/{'a' * 24}", json={"file": "IMG_0042.MOV"})
    assert r.status == 200 and calls == [("a" * 24, engine.files_dir(HOME) / "IMG_0042.MOV")]
    assert (await client.post(f"/api/telegram-files/{'b' * 24}", json={"file": "IMG_0042.MOV"})).status == 404


async def test_a_video_ken_shares_plays_in_the_chat_and_downloads_with_one_tap(engine, aiohttp_client, hosted):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    reel = engine.home_cwd / "reel.mp4"
    reel.parent.mkdir(parents=True, exist_ok=True)
    reel.write_bytes(b"\0" * 10)
    shown = await client.get("/api/file", params={"path": "reel.mp4"})
    assert shown.status == 200 and shown.headers["Content-Disposition"].startswith("inline")
    saved = await client.get("/api/file?path=reel.mp4&download")
    assert saved.status == 200 and saved.headers["Content-Disposition"].startswith("attachment")
    assert "media-save" in (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()


async def test_a_link_ken_never_sent_goes_out_when_it_starts_again(hosted):
    token = bot.wait_for_big_file(chat_id=777001, message_id=343, caption="edit this for my insta", kind="a video",
                                  name="IMG_5553.MOV", size=25 * MB)  # saved, then Ken stopped before replying
    sent = []

    class Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text, kw["reply_parameters"].message_id))
    await bot.resend_big_file_links(Bot())
    assert sent == [(777001, f"Sorry, I restarted before I could answer. That video is too big for Telegram to pass on. "
                             f"Send it here: {SITE}/send/{token}", 343)]
    await bot.resend_big_file_links(Bot())
    assert len(sent) == 1  # once
    assert bot.waiting_big_file(token)["caption"] == "edit this for my insta"  # still waiting for the file


def test_a_reel_too_big_to_send_gets_a_link_straight_to_it(monkeypatch, tmp_path):
    import bot
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://ken.example/box/abc/keepalive")
    monkeypatch.setattr(bot, "INBOX_DIR", tmp_path / "inbox")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "porto reel.mp4").write_bytes(b"older")
    out = tmp_path / "porto reel.mp4"
    out.write_bytes(b"new")
    said = bot.download_where(out)
    assert said == "Download it here: https://ken.example/download/porto%20reel-2.mp4"
    assert (tmp_path / "inbox" / "porto reel-2.mp4").read_bytes() == b"new" and not out.exists()
    monkeypatch.delenv("KEN_KEEPALIVE_URL")
    assert bot.download_where(tmp_path / "x.mp4") == f"It's on this computer at {tmp_path / 'x.mp4'}"


async def test_a_file_telegram_times_out_on_comes_as_a_link_not_an_error(monkeypatch, tmp_path):
    import bot
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://ken.example/box/abc/keepalive")
    monkeypatch.setattr(bot, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(bot, "OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setattr(bot, "log_history", lambda *a: None)
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox" / "plan.pdf").write_bytes(b"pdf")
    said = []

    class Bot:
        async def send_document(self, *a, **kw):
            raise TimeoutError("Timed out")

        async def send_message(self, chat_id, text, **kw):
            said.append(text)

    await bot.flush_outbox(Bot(), 1)
    assert said == ["📁 plan.pdf didn't go through on Telegram. Download it here: https://ken.example/download/plan.pdf"]
    assert (tmp_path / "inbox" / "plan.pdf").read_bytes() == b"pdf"


async def test_a_batch_arrives_together_and_ken_works_on_all_of_it(hosted, monkeypatch, tmp_path):
    msg = Msg("one reel from these")
    await bot.too_big(msg, SimpleNamespace(file_size=50 * MB), "a video", "a.MOV")
    await bot.too_big(Msg(""), SimpleNamespace(file_size=50 * MB), "a video", "b.MOV")
    token = msg.replies[0].rsplit("/", 1)[1]
    asked = {}

    async def send_message(pid, text, files=None, **kw):
        asked.update(text=text, files=files, **kw)
        return True
    monkeypatch.setattr(bot, "ENGINE", SimpleNamespace(_bg=[], send_message=send_message))
    monkeypatch.setattr(bot, "log_history", lambda *a: None)
    phone = bot.Phone()
    phone.app = SimpleNamespace(bot=None)
    paths = [tmp_path / "a.MOV", tmp_path / "b.MOV"]
    assert await phone.file_arrived(token, paths)
    await asyncio.gather(*bot.ENGINE._bg)
    assert asked["files"] == ["a.MOV", "b.MOV"] and "2 files together" in asked["prompt"] and str(paths[1]) in asked["prompt"]
    assert 'Their message with them: "one reel from these"' in asked["prompt"]


async def test_the_web_app_takes_a_batch(engine, aiohttp_client, hosted):  # noqa: F811
    calls = []

    class FakePhone:
        def waiting_file(self, token):
            return {"kind": "a video", "name": "a.MOV", "caption": "", "size": 1, "files": []}

        async def file_arrived(self, token, paths):
            calls.append(paths)
            return True

    app, client = await signed_in(engine, aiohttp_client, phone=FakePhone())
    engine.files_dir(HOME).mkdir(parents=True, exist_ok=True)
    for n in ("a.MOV", "b.MOV"):
        (engine.files_dir(HOME) / n).write_bytes(b"v")
    assert (await client.post(f"/api/telegram-files/{'a' * 24}", json={"files": ["a.MOV", "../b"]})).status == 400
    assert (await client.post(f"/api/telegram-files/{'a' * 24}", json={"files": ["a.MOV", "b.MOV"]})).status == 200
    assert calls == [[engine.files_dir(HOME) / "a.MOV", engine.files_dir(HOME) / "b.MOV"]]


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
async def test_a_big_video_plays_from_a_small_streaming_ready_copy(engine, aiohttp_client, hosted, tmp_path):  # noqa: F811
    """Phones choke on big originals saved without faststart; ?preview=1 serves a small H.264 copy that starts at once,
    made once and kept. Without it (saving, sharing) the original comes back untouched."""
    import subprocess
    app, client = await signed_in(engine, aiohttp_client)
    engine.files_dir(HOME).mkdir(parents=True, exist_ok=True)
    big = engine.files_dir(HOME) / "reel.mov"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(big)], check=True)  # no faststart: moov at the end
    original = big.read_bytes()
    r = await client.get(f"/api/chats/{HOME}/files/reel.mov?preview=1")
    preview = await r.read()
    assert r.status == 200 and preview != original
    assert 0 <= preview.find(b"moov") < preview.find(b"mdat")  # streaming-ready
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", "-"], input=preview, capture_output=True, check=True).stdout.decode().strip()
    assert probe == "1280,720"
    assert len(list(app.previews.glob("*.mp4"))) == 1
    await client.get(f"/api/chats/{HOME}/files/reel.mov?preview=1")
    assert len(list(app.previews.glob("*.mp4"))) == 1  # made once
    assert await (await client.get(f"/api/chats/{HOME}/files/reel.mov")).read() == original  # saving gets the original


@pytest.mark.skipif(sys.platform == "win32", reason="Ken in the cloud and on a Mac write POSIX paths")
def test_a_reply_never_shows_a_path_a_cloud_ken_links_the_file(monkeypatch, tmp_path):
    import bot
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://ken.example/box/abc/keepalive")
    monkeypatch.setattr(bot, "INBOX_DIR", tmp_path / "inbox")
    reel = tmp_path / "work" / "videos" / "launch-x" / "launch-x.mp4"
    reel.parent.mkdir(parents=True)
    reel.write_bytes(b"reel")
    notes = tmp_path / "work" / "notes.md"
    notes.write_text("x")
    said = bot.no_paths(f"It's saved at `{reel}`. I also updated {notes}.")
    assert said == "It's saved at launch-x.mp4 (https://ken.example/download/launch-x.mp4). I also updated notes.md."
    assert (tmp_path / "inbox" / "launch-x.mp4").read_bytes() == b"reel" and reel.exists()  # linked, not moved
    assert bot.no_paths(f"Again: {reel}") == "Again: launch-x.mp4 (https://ken.example/download/launch-x.mp4)"
    assert bot.no_paths("Go to https://kencomputer.dev/a/b.html now") == "Go to https://kencomputer.dev/a/b.html now"


@pytest.mark.skipif(sys.platform == "win32", reason="Ken in the cloud and on a Mac write POSIX paths")
def test_a_reply_never_shows_a_path_a_ken_at_home_sends_the_file(monkeypatch, tmp_path):
    import bot
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)
    monkeypatch.setattr(bot, "OUTBOX_DIR", tmp_path / "outbox")
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"reel")
    assert bot.no_paths(f"Here it is: [the reel]({reel})") == "Here it is: the reel"
    assert bot.no_paths(f"Here: {reel}") == "Here: reel.mp4"
    assert (tmp_path / "outbox" / "reel.mp4").read_bytes() == b"reel"


async def test_ken_makes_a_link_itself_when_it_needs_a_file_at_full_quality(monkeypatch, tmp_path):
    """Telegram shrinks videos: Ken runs `--upload-link`, gives them the link, and carries on in Telegram when it arrives."""
    import subprocess
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "KEN_HOME": str(home), "KEN_KEEPALIVE_URL": f"{SITE}/box/checkin"}
    out = subprocess.run([sys.executable, str(Path(bot.__file__)), "--upload-link", "the original video, full quality"],
                         env=env, capture_output=True, text=True, timeout=60)
    token = re.fullmatch(rf"{SITE}/send/([0-9a-f]{{24}})\n", out.stdout).group(1)
    monkeypatch.setattr(bot, "BIG_FILES", home / "telegram-big-files.json")
    monkeypatch.setattr(bot, "ALLOWED_USER_ID", 555)
    sent, asked = [], {}

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))

    async def send_message(pid, text, files=None, **kw):
        asked.update(pid=pid, text=text, files=files, **kw)
        await kw["listener"]("Starting the edit.")
        return True

    monkeypatch.setattr(bot, "ENGINE", SimpleNamespace(_bg=[], send_message=send_message))
    monkeypatch.setattr(bot, "log_history", lambda *a: None)
    monkeypatch.setattr(bot, "flush_outbox", lambda *a, **k: asyncio.sleep(0))
    phone = bot.Phone()
    phone.app = SimpleNamespace(bot=FakeBot())
    assert phone.waiting_file(token) | {} == {"kind": "", "name": "", "caption": "the original video, full quality", "size": 0, "files": [], "asked": True}
    assert await phone.file_arrived(token, tmp_path / "IMG_1.MOV")
    await asyncio.gather(*bot.ENGINE._bg)
    assert sent == [(555, "Starting the edit.")] and asked["via"] == "telegram"
    # on a computer of its own there's no link: it says to ask where the file is
    off = subprocess.run([sys.executable, str(Path(bot.__file__)), "--upload-link"], env={**env, "KEN_KEEPALIVE_URL": ""},
                         capture_output=True, text=True, timeout=60)
    assert off.returncode == 1 and "ask them where the file is" in off.stdout
    assert "--upload-link" in bot.HOSTED_FILES_PROMPT and "Never ask them to resend it as a file" in bot.HOSTED_FILES_PROMPT
