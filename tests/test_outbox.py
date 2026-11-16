import asyncio

import bot


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_video(self, chat_id, fh, **kw):
        self.sent.append(("video", kw.get("filename")))

    async def send_document(self, chat_id, fh, **kw):
        self.sent.append(("file", kw.get("filename")))

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("text", text))

    async def send_photo(self, chat_id, fh, **kw):
        self.sent.append(("photo", None))


def run(monkeypatch, tmp_path, size, env=None, encode=None):
    outbox = tmp_path / "outbox"
    outbox.mkdir(exist_ok=True)
    reel = outbox / "reel.mp4"
    reel.write_bytes(b"x" * size)
    monkeypatch.setattr(bot, "OUTBOX_DIR", outbox)
    monkeypatch.setattr(bot, "TELEGRAM_FILE_LIMIT", 100)
    monkeypatch.setattr(bot, "log_history", lambda *a, **k: None)

    async def for_telegram(path, limit):
        return path, {}  # pretend it already fits as a playable video
    monkeypatch.setattr(bot.video, "for_telegram", encode or for_telegram)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    fake = FakeBot()
    asyncio.run(bot.flush_outbox(fake, 1))
    return fake.sent, reel


def test_a_reel_that_plays_as_is_comes_once(monkeypatch, tmp_path):
    """The playable video already is the original: a second, identical file was the video twice in the chat."""
    sent, reel = run(monkeypatch, tmp_path, 50)
    assert sent == [("video", "reel.mp4")]
    assert not reel.exists()


def test_a_reel_re_encoded_to_play_also_comes_full_quality(monkeypatch, tmp_path):
    async def for_telegram(path, limit):
        out = tmp_path / "playable.mp4"
        out.write_bytes(b"y" * 40)
        return out, {}
    sent, reel = run(monkeypatch, tmp_path, 50, encode=for_telegram)
    assert sent == [("video", "reel.mp4"), ("file", "reel.mp4")]
    assert not reel.exists() and not (tmp_path / "playable.mp4").exists()


def test_the_same_video_put_in_twice_goes_once(monkeypatch, tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "clip-copy.mp4").write_bytes(b"x" * 50)
    sent, reel = run(monkeypatch, tmp_path, 50)
    assert sent == [("video", "clip-copy.mp4")]
    assert not reel.exists() and not (outbox / "clip-copy.mp4").exists()


def test_a_reel_too_big_for_telegram_says_where_to_download_it(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "INBOX_DIR", tmp_path / "inbox")
    async def shrunk(path, limit):
        out = tmp_path / "playable.mp4"
        out.write_bytes(b"y" * 90)
        return out, {}
    sent, _ = run(monkeypatch, tmp_path, 500, {"KEN_KEEPALIVE_URL": "https://ken.example/box/b1/keepalive"}, encode=shrunk)
    assert sent[0] == ("video", "reel.mp4")
    assert sent[1][0] == "text" and "Download it here: https://ken.example/download/reel.mp4" in sent[1][1]
    assert (tmp_path / "inbox" / "reel.mp4").stat().st_size == 500  # kept in Ken's files, where the link finds it
