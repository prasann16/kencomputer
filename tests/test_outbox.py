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


def run(monkeypatch, tmp_path, size, env=None):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    reel = outbox / "reel.mp4"
    reel.write_bytes(b"x" * size)
    monkeypatch.setattr(bot, "OUTBOX_DIR", outbox)
    monkeypatch.setattr(bot, "TELEGRAM_FILE_LIMIT", 100)
    monkeypatch.setattr(bot, "log_history", lambda *a, **k: None)

    async def for_telegram(path, limit):
        return path, {}  # pretend it already fits as a playable video
    monkeypatch.setattr(bot.video, "for_telegram", for_telegram)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    fake = FakeBot()
    asyncio.run(bot.flush_outbox(fake, 1))
    return fake.sent, reel


def test_a_reel_goes_as_a_video_and_the_full_quality_file(monkeypatch, tmp_path):
    sent, reel = run(monkeypatch, tmp_path, 50)
    assert sent == [("video", "reel.mp4"), ("file", "reel.mp4")]
    assert not reel.exists()


def test_a_reel_too_big_for_telegram_says_where_to_download_it(monkeypatch, tmp_path):
    sent, _ = run(monkeypatch, tmp_path, 500, {"KEN_INCLUDED_AI_URL": "https://ken.example/ai"})
    assert sent[0] == ("video", "reel.mp4")
    assert sent[1][0] == "text" and "Open Ken to download it: https://ken.example" in sent[1][1]
