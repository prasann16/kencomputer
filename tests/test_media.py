"""Hosted Ken's creator storage client (media.py): a file goes up in pieces and comes back, links and lists work, and
Ken only offers storage where its host has it."""
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import media  # noqa: E402

SITE = "https://app.example.dev"


class FakeHost:
    """The host's /box/media/* routes, in memory."""

    def __init__(self):
        self.calls, self.parts, self.stored = [], {}, {}

    def __call__(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        assert req.headers["Authorization"] == "Bearer check-1"
        path = url[len(SITE):]
        self.calls.append((method, path.split("?")[0]))
        body = req.data
        if path == "/box/media/start":
            out = {"key": "u/u1/2026-11/abc-" + json.loads(body)["name"], "upload": "up1", "part": 4}
        elif path.startswith("/box/media/part"):
            n = int(path.rsplit("n=", 1)[1])
            self.parts[n] = body
            out = {"etag": f"e{n}", "n": n}
        elif path == "/box/media/done":
            d = json.loads(body)
            self.stored[d["key"]] = b"".join(self.parts[p["n"]] for p in d["parts"])
            out = {"key": d["key"], "size": len(self.stored[d["key"]])}
        elif path.startswith("/box/media/file"):
            key = urllib.request.unquote(path.split("key=")[1])
            data = self.stored[key]
            rng = req.headers.get("Range")
            if rng:
                a, b = rng.split("=")[1].split("-")
                data = data[int(a):int(b) + 1]
            return Resp(data)
        elif path == "/box/media/link":
            out = {"url": f"{SITE}/m/signed"}
        else:
            raise AssertionError(path)
        return Resp(json.dumps(out).encode())


class Resp:
    def __init__(self, data):
        self.data, self.at = data, 0

    def read(self, n=-1):
        chunk = self.data[self.at:] if n < 0 else self.data[self.at:self.at + n]
        self.at += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_a_file_goes_up_in_pieces_and_comes_back(monkeypatch, tmp_path):
    monkeypatch.setenv("KEN_KEEPALIVE_URL", f"{SITE}/box/checkin")
    monkeypatch.setenv("KEN_KEEPALIVE_TOKEN", "check-1")
    host = FakeHost()
    monkeypatch.setattr(media.urllib.request, "urlopen", host)
    src = tmp_path / "IMG_7613.MOV"
    src.write_bytes(b"0123456789")  # pieces of 4 bytes: three pieces
    stored = media.put(src)
    assert stored == {"key": "u/u1/2026-11/abc-IMG_7613.MOV", "size": 10}
    assert [c for c in host.calls if c[1] == "/box/media/part"] == [("PUT", "/box/media/part")] * 3
    assert media.get(stored["key"], tmp_path / "back.mov").read_bytes() == b"0123456789"
    assert media.get(stored["key"], tmp_path / "part.mov", "2-5").read_bytes() == b"2345"
    assert media.link(stored["key"]) == f"{SITE}/m/signed"


def test_storage_is_only_offered_by_a_host_that_has_it(monkeypatch, capsys):
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)
    assert not media.available()
    assert media.main(["list"]) == 1
    assert "only on Ken in the cloud" in capsys.readouterr().out
    import bot
    assert bot.storage_prompt() == ""
    monkeypatch.setenv("KEN_KEEPALIVE_URL", f"{SITE}/box/checkin")
    monkeypatch.setenv("KEN_KEEPALIVE_TOKEN", "check-1")
    assert "--media put" in bot.storage_prompt()
    assert not bot.can_send_from_storage(60 * 1024 * 1024)  # the host hasn't switched Telegram over
    monkeypatch.setenv("TELEGRAM_SEND_FROM_STORAGE", "1")
    assert bot.can_send_from_storage(60 * 1024 * 1024) and not bot.can_send_from_storage(3 * 1024 ** 3)
