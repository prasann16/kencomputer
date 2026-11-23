"""Hosted Ken: a file too big for Telegram to pass on (over 20 MB) gets a one-tap link at once, the web app takes the
file instead, and Ken carries on with what was asked, answering in Telegram. The same for the shared bot and own bots
(both reach Ken as pushed updates)."""
import asyncio
import json
import os
import re
import sys
import tempfile
import time
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
    token = re.fullmatch(rf"Telegram can't pass on a video this big\. Send it to me here instead, at full quality: {SITE}/send/([0-9a-f]{{24}})", reply).group(1)
    w = bot.waiting_big_file(token)
    assert w | {"ts": 0, "last": 0} == {"chat_id": 777001, "message_id": 42, "caption": "make a reel of this", "kind": "a video",
                                        "name": "IMG_0042.MOV", "size": 180 * MB, "ts": 0, "last": 0, "replied": True, "reply_id": 901,
                                        "files": [{"kind": "a video", "name": "IMG_0042.MOV", "size": 180 * MB}]}
    other = Msg("", chat_id=555)
    assert await bot.too_big(other, SimpleNamespace(file_size=30 * MB), "a file", "notes.pdf")
    assert other.replies[0].startswith("Telegram can't pass on a file this big.")
    later = Msg("", chat_id=556)
    assert await bot.too_big(later, SimpleNamespace(file_size=40 * MB), "a recording", "call.m4a")
    assert later.replies[0].startswith("Telegram can't pass on a recording this big")


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
    assert EDITS[-1] == (777001, 901, f"Telegram can't pass on these 3 videos, they're too big. Send them all to me here instead: {SITE}/send/{token}")
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


async def test_links_expire_after_three_days(hosted, monkeypatch):
    msg = Msg()
    await bot.too_big(msg, SimpleNamespace(file_size=50 * MB), "a video", "a.mp4")
    token = msg.replies[0].rsplit("/", 1)[1]
    now = bot.time.time()
    monkeypatch.setattr(bot.time, "time", lambda: now + 3 * 86400 + 1)
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


async def test_the_send_page_uploads_in_pieces_only_while_its_link_waits(engine, aiohttp_client, hosted):  # noqa: F811
    """The page behind a send-it-here link has no sign-in: its link lets it add files for that request, nothing else."""
    class FakePhone:
        def waiting_file(self, token):
            return {"kind": "", "name": "", "caption": "clips for the reel", "size": 0} if token == "a" * 24 else None

    app, client = await signed_in(engine, aiohttp_client, phone=FakePhone())
    base = f"/api/telegram-files/{'a' * 24}/uploads"
    uid = (await (await client.post(base, json={"name": "clip.mov", "size": 10})).json())["id"]
    assert (await (await client.put(f"{base}/{uid}?offset=0", data=b"01234")).json()) == {"received": 5}
    assert (await (await client.put(f"{base}/{uid}?offset=5", data=b"56789")).json()) == {"received": 10}
    saved = (await (await client.post(f"{base}/{uid}/done")).json())["saved"]
    assert (engine.files_dir(HOME) / saved[0]).read_bytes() == b"0123456789"
    # A link that isn't waiting (expired, used, or made up) can't start, add to or finish anything.
    other = f"/api/telegram-files/{'b' * 24}/uploads"
    assert (await client.post(other, json={"name": "x.mov", "size": 1})).status == 404
    assert (await client.put(f"{other}/{uid}?offset=0", data=b"x")).status == 404
    assert (await client.post(f"{other}/{uid}/done")).status == 404


def test_files_are_explained_where_they_write_from_not_in_every_chat(monkeypatch):
    """Telegram's limits ride with Telegram messages only; in the app, Ken knows attachments arrive whole."""
    import engine as engine_mod
    monkeypatch.setattr(engine_mod, "WHERE", dict(engine_mod.WHERE))
    bot.where_notes_for_hosted()
    assert "20 MB" in engine_mod.WHERE["telegram"] and "--upload-link" in engine_mod.WHERE["telegram"]
    assert "20 MB" not in engine_mod.WHERE["app"] and "full quality" in engine_mod.WHERE["app"]
    assert "20 MB" not in bot.SYSTEM_PROMPT and "upload-link" not in bot.build_system()


def test_a_send_link_names_the_computer_so_the_site_can_open_it_without_a_sign_in(monkeypatch):
    monkeypatch.setenv("AGENT37_INSTANCE_ID", "abc123xyz0")
    assert bot.send_url(SITE, "f" * 24) == f"{SITE}/send/abc123xyz0/{'f' * 24}"
    monkeypatch.setenv("AGENT37_INSTANCE_ID", "../x")
    assert bot.send_url(SITE, "f" * 24) == f"{SITE}/send/{'f' * 24}"


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
    assert sent == [(777001, f"Sorry, I restarted before I could answer. Telegram can't pass on a video this big. "
                             f"Send it to me here instead, at full quality: {SITE}/send/{token}", 343)]
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


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
async def test_a_reel_ken_names_is_ready_to_watch_before_anyone_presses_play(engine, aiohttp_client, hosted):  # noqa: F811
    """The watching copy is made when Ken's reply names the video (in code, a link, or a bare path), not on the first
    play, which on a phone meant a long black box. Your own messages and files that aren't videos start nothing."""
    import subprocess
    app, client = await signed_in(engine, aiohttp_client)
    engine.files_dir(HOME).mkdir(parents=True, exist_ok=True)
    reel = engine.files_dir(HOME) / "reel.mov"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(reel)], check=True)
    (engine.files_dir(HOME) / "notes.txt").write_text("x")
    engine.log_message(HOME, "you", f"edit `{reel}` please")
    await asyncio.sleep(0.3)
    assert not list(app.previews.glob("*.mp4"))  # what they said: nothing made
    engine.log_message(HOME, "ken", f"Done, here it is: `{reel}`. Notes in {engine.files_dir(HOME) / 'notes.txt'}.")
    for _ in range(100):
        if list(app.previews.glob("*.mp4")) and not list(app.previews.glob("*.part.mp4")):
            break
        await asyncio.sleep(0.1)
    assert len(list(app.previews.glob("*.mp4"))) == 1
    await client.get(f"/api/chats/{HOME}/files/reel.mov?preview=1")
    assert len(list(app.previews.glob("*.mp4"))) == 1  # the phone gets that same copy, not a second one


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
async def test_the_watching_copy_goes_up_to_the_site_so_it_plays_without_waking_ken(engine, aiohttp_client, hosted, monkeypatch):  # noqa: F811
    """Hosted with their Library on: each watching copy also goes up to the site (ken-hosted previews.ts), named the way
    the chat names it, once; the phone and the web then play it from storage. The site saying no (Library off) stops
    the asking for a while, and nothing about playing from this computer changes."""
    import subprocess
    from aiohttp import web as aioweb
    calls, state = [], {"on": True, "has": set()}

    async def site(request):
        if not state["on"]:
            calls.append((request.method, request.path, None))
            return aioweb.json_response({"error": "not_found"}, status=404)
        assert request.headers["Authorization"] == "Bearer box-token"
        key = request.query.get("key")
        body = await request.read()
        calls.append((request.method, request.path, key or json.loads(body)))
        if request.path.endswith("/has"):
            return aioweb.json_response({"has": key in state["has"]})
        if request.path.endswith("/file"):
            assert body[:12].find(b"ftyp") >= 0 and int(request.headers["Content-Length"]) == len(body)
            state["has"].add(key)
        return aioweb.json_response({"ok": True})

    fake = aioweb.Application()
    fake.router.add_route("*", "/box/previews/{what}", site)
    site_client = await aiohttp_client(fake)
    monkeypatch.setenv("KEN_KEEPALIVE_URL", f"http://127.0.0.1:{site_client.port}/box/checkin")
    monkeypatch.setenv("KEN_KEEPALIVE_TOKEN", "box-token")

    app, client = await signed_in(engine, aiohttp_client)
    engine.files_dir(HOME).mkdir(parents=True, exist_ok=True)
    reel = engine.files_dir(HOME) / "reel.mov"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(reel)], check=True)

    async def settled(n):
        for _ in range(100):
            if len(calls) >= n and not app.bg:
                return
            await asyncio.sleep(0.05)

    engine.log_message(HOME, "ken", f"Here it is: `{reel}`")
    await settled(3)
    [copy] = app.previews.glob("*.mp4")
    assert calls == [("GET", "/box/previews/has", copy.stem), ("PUT", "/box/previews/file", copy.stem),
                     ("POST", "/box/previews/names", {"key": copy.stem, "refs": [str(reel)]})]
    # Played as a chat file: only its new name goes up, not the video again. Played again: nothing.
    r = await client.get(f"/api/chats/{HOME}/files/reel.mov?preview=1")
    assert r.status == 200 and await r.read() == copy.read_bytes()
    await settled(4)
    assert calls[3:] == [("POST", "/box/previews/names", {"key": copy.stem, "refs": [f"chat:{HOME}/reel.mov"]})]
    await client.get(f"/api/chats/{HOME}/files/reel.mov?preview=1")
    await settled(5)
    assert len(calls) == 4
    # Their Library switched off: the site says no once, and playing still works as it did.
    state["on"] = False
    reel.write_bytes(reel.read_bytes())  # a new version: a new copy
    os.utime(reel, (time.time() + 5, time.time() + 5))
    for _ in range(2):
        assert (await client.get(f"/api/chats/{HOME}/files/reel.mov?preview=1")).status == 200
        await settled(6)
    assert calls[4:] == [("GET", "/box/previews/has", None)]


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


# ------------------------------------------------- a file Ken made, too big for Telegram: a sign-in-free download link

def test_a_download_link_names_the_computer_and_a_key_for_that_one_file(monkeypatch, tmp_path):
    import bot
    import web
    monkeypatch.setattr(bot, "KEN_HOME", tmp_path)
    monkeypatch.setattr(bot, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://ken.example/box/abc/keepalive")
    monkeypatch.setenv("AGENT37_INSTANCE_ID", "abc123xyz0")
    out = tmp_path / "episode 4.mp3"
    out.write_bytes(b"x" * 100)
    said = bot.download_where(out)
    m = re.fullmatch(r"Download it here: https://ken\.example/get/abc123xyz0/([0-9a-f]{24})/episode%204\.mp3", said)
    assert m and web.shared_download(tmp_path, m.group(1)) == "episode 4.mp3"
    if sys.platform != "win32":
        assert oct((tmp_path / web.DOWNLOAD_LINKS).stat().st_mode & 0o777) == "0o600"
    # The same file named again keeps its link; another file gets its own key.
    assert bot.download_url("https://ken.example", "episode 4.mp3") == said.split(": ", 1)[1]
    assert web.share_download(tmp_path, "other.mp3") != m.group(1)
    # A computer without a usable name: the old link (sign in first).
    monkeypatch.setenv("AGENT37_INSTANCE_ID", "../x")
    assert bot.download_url("https://ken.example", "a b.mp4") == "https://ken.example/download/a%20b.mp4"


def test_a_download_key_expires_after_a_week(monkeypatch, tmp_path):
    import web
    key = web.share_download(tmp_path, "reel.mp4")
    assert web.shared_download(tmp_path, key) == "reel.mp4"
    assert web.shared_download(tmp_path, "0" * 24) is None and web.shared_download(tmp_path, "../x") is None
    later = web.time.time() + web.DOWNLOAD_TTL + 1
    monkeypatch.setattr(web.time, "time", lambda: later)
    assert web.shared_download(tmp_path, key) is None
    assert web.share_download(tmp_path, "reel.mp4") != key  # a new link, the old one gone


async def test_a_download_link_hands_over_exactly_its_file_as_a_download(engine, aiohttp_client):
    import web
    app, client = await signed_in(engine, aiohttp_client)
    files = engine.files_dir(HOME)
    files.mkdir(parents=True, exist_ok=True)
    body = bytes(range(256)) * 4096  # 1 MB
    (files / "episode 4.mp3").write_bytes(body)
    key = web.share_download(engine.home, "episode 4.mp3")
    r = await client.get(f"/api/telegram-downloads/{key}")
    assert r.status == 200 and await r.read() == body
    assert r.headers["Content-Disposition"] == "attachment; filename*=UTF-8''episode%204.mp3"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    for _ in range(100):  # the server lets go of it just after the last byte reaches the client
        if not web.DOWNLOADS:
            break
        await asyncio.sleep(0.01)
    assert not web.DOWNLOADS  # done going out (and while it was: test_a_download_going_out_keeps_ken_busy)
    # A big file can be fetched in parts, and resumed.
    part = await client.get(f"/api/telegram-downloads/{key}", headers={"Range": "bytes=1000-1999"})
    assert part.status == 206 and await part.read() == body[1000:2000]
    assert part.headers["Content-Range"] == f"bytes 1000-1999/{len(body)}"
    assert (await client.head(f"/api/telegram-downloads/{key}")).status == 200
    # Wrong, malformed or expired keys, and a file that's gone: the same plain answer.
    gone = "This link has expired. Ask Ken for a new one."
    for bad in ("0" * 24, "abc", "..%2F..%2Fsecret"):
        r = await client.get(f"/api/telegram-downloads/{bad}")
        assert r.status == 404 and (await r.json())["error"] == gone
    links = json.loads((engine.home / web.DOWNLOAD_LINKS).read_text())
    links[key]["ts"] -= web.DOWNLOAD_TTL + 1
    (engine.home / web.DOWNLOAD_LINKS).write_text(json.dumps(links))
    assert (await client.get(f"/api/telegram-downloads/{key}")).status == 404
    # The site needs Ken's own token for it (the key alone, without the site, opens nothing).
    key = web.share_download(engine.home, "episode 4.mp3")
    client.session.cookie_jar.clear()
    assert (await client.get(f"/api/telegram-downloads/{key}")).status == 401


async def test_a_download_link_never_reaches_past_homes_files(engine, aiohttp_client, tmp_path):
    import web
    app, client = await signed_in(engine, aiohttp_client)
    (engine.home / "secret.env").write_text("TOKEN=1")
    engine.files_dir(HOME).mkdir(parents=True, exist_ok=True)
    (engine.files_dir(HOME) / ".hidden").write_text("x")
    if sys.platform != "win32":  # making a link there needs extra rights
        (engine.files_dir(HOME) / "out").symlink_to(engine.home / "secret.env")
    (engine.home / web.DOWNLOAD_LINKS).write_text(json.dumps({
        k * 24: {"name": n, "ts": web.time.time()} for k, n in
        (("a", "../secret.env"), ("b", "..\\secret.env"), ("c", ".hidden"), ("d", "out"), ("e", ""), ("f", "nope.mp4"))}))
    for k in "abcdef":
        r = await client.get(f"/api/telegram-downloads/{k * 24}")
        assert r.status == 404, k


async def test_a_download_going_out_keeps_ken_busy(monkeypatch, tmp_path):
    """A big file takes many minutes: while it goes out, Ken isn't quiet (no update restart) and tells its host it's
    busy (bot.py keep_awake), so the computer doesn't fall asleep halfway."""
    import web
    seen = []

    async def sending(self, request):
        seen.append((bool(web.DOWNLOADS), web.quiet_seconds()))

    monkeypatch.setattr(web.web.FileResponse, "prepare", sending)
    (tmp_path / "a.mp3").write_bytes(b"x")
    await web._Download(tmp_path / "a.mp3").prepare(None)
    assert seen == [(True, 0.0)] and not web.DOWNLOADS
