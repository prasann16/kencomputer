"""Their Library on Ken's side (library.py): reading when and where a photo or video was taken, naming the place here
(offline), what Ken's search shows, and staying out of the way where there's no Library."""
import json

import library
from places import place_name


def test_when_and_where_from_cameras():
    assert library._when("2026:10:12 18:03:11") == 1791828191000
    assert library._when("2026-10-12T18:03:11.000000Z") == 1791828191000
    assert library._when("0000:00:00 00:00:00") is None and library._when("") is None
    assert library._iso6709("+38.7223-009.1393+012.000/") == (38.7223, -9.1393)
    lat = library._dms("38/1, 43/1, 2028/100", "N")
    lon = library._dms("9/1, 8/1, 2148/100", "W")
    assert round(lat, 4) == 38.7223 and round(lon, 4) == -9.1393


def test_place_names_are_worked_out_here():
    assert place_name(38.7223, -9.1393) == "Lisbon, Portugal"
    assert place_name(41.1579, -8.6291) == "Porto, Portugal"
    assert place_name(35.68, 139.69) == "Tokyo, Japan"  # the city, not the district
    assert place_name(0, -150) == "" and place_name(None, None) == ""


def test_find_shows_ken_what_it_needs(monkeypatch, capsys, tmp_path):
    asked = []

    def fake(path, body=None, **kw):
        asked.append(path)
        return {"total": 40, "items": [{"id": "a" * 16, "kind": "video", "taken_at": 1791828191000, "place": "Lisbon, Portugal",
                                         "duration": 31.4, "width": 1080, "height": 1920, "name": "IMG_2.MOV", "album": None,
                                         "note": "Sunset over the river"}]}

    monkeypatch.setattr(library, "call", fake)
    assert library.main(["find", "lisbon", "--kind", "video", "--from", "2026-10-01"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "1 found (of 40" in out and "Lisbon, Portugal · 31s · vertical · IMG_2.MOV" in out and "note: Sunset over the river" in out
    assert asked[0].startswith("find?") and "q=lisbon" in asked[0] and "kind=video" in asked[0] and "from=1790812800000" in asked[0]


def test_no_library_no_noise(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)
    assert library.site() is None
    assert library.process_new() is None  # not hosted: nothing to do, nothing logged
    assert library.main(["find"], tmp_path) == 1
    assert "isn't on" in capsys.readouterr().out


def test_process_reads_a_photo_and_sends_back_its_thumbnail_and_details(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(library, "file_url", lambda i: ("https://site/box/library/file?id=" + i, "tok"))
    monkeypatch.setattr(library, "_download", lambda *a: True)

    class Resp:
        def __init__(self): self.data = b"jpegbytes"
        def read(self, *a): d, self.data = self.data, b""; return d
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(library.urllib.request, "urlopen", lambda *a, **k: Resp())
    monkeypatch.setattr(library, "photo_facts", lambda p: {"kind": "photo", "taken_at": 1, "lat": 38.7223, "lon": -9.1393, "width": 4, "height": 3})

    def thumb(src, out, **kw):
        out.write_bytes(b"thumb")
        return True

    monkeypatch.setattr(library, "make_thumb", thumb)
    monkeypatch.setattr(library, "call", lambda path, body=None, **kw: sent.append((path, body)))
    library.process({"id": "b" * 16, "name": "IMG_1.HEIC", "kind": "photo"}, tmp_path)
    assert sent[0] == (f"thumb?id={'b' * 16}", b"thumb")
    assert sent[1][0] == "meta" and sent[1][1]["place"] == "Lisbon, Portugal" and sent[1][1]["taken_at"] == 1
    assert not list(tmp_path.iterdir())  # nothing left behind on this computer
    json.dumps(sent[1][1])


def test_show_puts_them_in_the_chat_ken_is_in(monkeypatch, tmp_path, capsys):
    a = "c" * 16
    monkeypatch.setenv("KEN_CHAT", "brand")
    monkeypatch.setattr(library, "call", lambda path, body=None, **kw: {"items": [{"id": a, "kind": "video", "name": "IMG_2.MOV", "place": "Lisbon, Portugal"}]})
    assert library.main(["show", a, "d" * 16], tmp_path) == 0
    req = json.loads(next((tmp_path / "requests").glob("*.json")).read_text())
    assert req == {"type": "library_show", "agent": "brand", "items": [{"id": a, "kind": "video", "name": "IMG_2.MOV", "place": "Lisbon, Portugal"}]}
    assert "Showing 1" in capsys.readouterr().out


def test_find_what_they_just_added(monkeypatch, capsys, tmp_path):
    asked = []
    now_ms = int(library.datetime.now().timestamp() * 1000)

    def fake(path, body=None, **kw):
        asked.append(path)
        return {"total": 28, "items": [{"id": "b" * 16, "kind": "video", "taken_at": 1779700000000, "name": "IMG_0158.MOV",
                                         "duration": 10, "added": now_ms}]}

    monkeypatch.setattr(library, "call", fake)
    assert library.main(["find", "--kind", "video", "--added-since", "today"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "IMG_0158.MOV · added today" in out
    since = int(dict(p.split("=") for p in asked[0].split("?", 1)[1].split("&"))["added_from"])
    assert 0 <= now_ms - since <= 86_400_000  # the start of this computer's day
    assert library._added(now_ms - 86_400_000).startswith(("added yesterday", "added today"))
