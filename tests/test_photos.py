"""Photos and videos shared from the phone app (photos.py): the originals stay on the phone; Ken keeps a small
thumbnail and its own note about each, finds them, asks the phone for full-size files to make a post, and suggests
clean-ups that only the person (and the phone's own confirm) can act on."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import aiohttp

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from photos import Photos, photos_tools  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
DAY = 86400
T = 1_790_000_000  # a fixed "taken at"


def share(p: Photos, asset="ABC-1/L0/001", kind="photo", frames=1, **meta):
    return p.add({"id": asset, "asset_id": asset, "kind": kind, "taken_at": T, **meta}, [JPEG] * frames)


def test_sharing_keeps_a_thumbnail_and_the_details_never_the_original(tmp_path):
    p = Photos(tmp_path)
    e = share(p, width=4032, height=3024, lat=38.72, lon=-9.14)
    assert e["id"] == "ABC-1-L0-001" and e["asset_id"] == "ABC-1/L0/001"
    assert p.thumb(e["id"]).read_bytes() == JPEG
    assert not (tmp_path / "photos" / "originals").exists()
    v = share(p, asset="VID-2", kind="video", frames=3, duration=14.2, frame_times=[0, 7, 13])
    assert v["frames"] == 3 and p.thumb("VID-2", 2)
    assert "video 14s" in p.find()


def test_where_it_was_taken_is_a_place_name_worked_out_here_and_favorites_and_screenshots_are_searchable(tmp_path):
    from places import place_name

    assert place_name(38.7223, -9.1393) == "Lisbon, Portugal"
    assert place_name(35.68, 139.69) == "Tokyo, Japan"  # the city, not the district the photo was in
    assert place_name(48.8566, 2.3522) == "Paris, France"
    assert place_name(0, -150) == "" and place_name(None, None) == ""  # the middle of the ocean, or no location
    p = Photos(tmp_path)
    share(p, asset="A", lat=38.7223, lon=-9.1393, favorite=True)
    share(p, asset="B", screenshot=True, subtypes=["screenshot"])
    assert "A ·" in p.find("lisbon") and "★ favorite" in p.find("lisbon")
    assert "A ·" in p.find("favorite") and "B ·" not in p.find("favorite")
    assert "B ·" in p.find("screenshot")


def test_events_group_photos_by_time_and_place_and_trips_are_away_from_home(tmp_path):
    p = Photos(tmp_path)
    home, lisbon = (41.15, -8.61), (38.7223, -9.1393)  # Porto, Lisbon
    for n in range(5):
        share(p, asset=f"H{n}", taken_at=T + n * DAY * 4, lat=home[0], lon=home[1])
    for n in range(3):
        share(p, asset=f"L{n}", taken_at=T + 30 * DAY + n * 3600, lat=lisbon[0], lon=lisbon[1])
    share(p, asset="S", taken_at=T + 30 * DAY + 4 * 3600)  # a screenshot mid-trip, no location: stays in the trip
    out = p.events()
    assert "home looks like Porto, Portugal" in out
    assert "Lisbon, Portugal" in out and "4 photos, a trip" in out
    assert out.index("Lisbon") < out.index("Porto, Portugal ·")  # newest first


def test_kens_note_is_what_finding_searches_and_sharing_again_keeps_it(tmp_path):
    p = Photos(tmp_path)
    share(p, asset="A")
    share(p, asset="B")
    assert "2 not looked at yet" in p.find()
    p.note("A", "Sunset at the beach in Lisbon, two people", ["beach", "sunset", "friends"], 8)
    assert "A ·" in p.find("beach") and "B ·" not in p.find("beach")
    assert "quality 8/10" in p.find("lisbon")
    share(p, asset="A")  # shared again from the phone
    assert p.index()["A"]["note"].startswith("Sunset")
    assert p.unnoted() == ["B"]
    assert "0 of 2" in p.find("", since="2030-01-01")


def test_looking_shows_ken_the_thumbnails_a_video_as_timed_frames(tmp_path):
    p = Photos(tmp_path)
    share(p, asset="V", kind="video", frames=2, duration=10, frame_times=[0, 8])
    out = p.look(["V", "missing"])
    images = [c for c in out if c["type"] == "image"]
    assert len(images) == 2 and images[0]["mimeType"] == "image/jpeg"
    assert any("frame at 8s" in c.get("text", "") for c in out)
    assert any("missing: not found" in c.get("text", "") for c in out)


def test_full_size_files_are_asked_of_the_phone_then_used_and_cleared_after_a_day(tmp_path):
    p = Photos(tmp_path)
    share(p, asset="A")
    said = p.want_originals(["A"])
    assert "Asked the phone for 1" in said and p.asks()["originals"] == ["A"]
    dest = p.original_path("A", "IMG_1.HEIC")
    dest.write_bytes(b"full size")
    p.got_original("A", dest)
    assert p.asks()["originals"] == []
    assert str(dest) in p.want_originals(["A"])
    os.utime(dest, (0, 0))  # a day and more later
    p.prune_originals()
    assert not dest.exists() and "original" not in p.index()["A"]


def test_clean_up_is_only_a_suggestion_they_choose_on_the_phone(tmp_path):
    p = Photos(tmp_path)
    share(p, asset="A")
    share(p, asset="B")
    p.suggest_removing([{"id": "A", "why": "blurry"}, {"id": "nope"}])
    assert p.asks()["remove"] == {"A": "blurry"}
    p.keep("A")
    assert p.asks()["remove"] == {}
    p.suggest_removing([{"id": "B", "why": "duplicate of A"}])
    assert p.remove("B")  # deleted on the phone: Ken forgets it, thumbnail and all
    assert "B" not in p.index() and not p.thumb("B") and p.asks()["remove"] == {}


def test_the_tools_ken_uses():
    home = Path(tempfile.mkdtemp())
    share(Photos(home), asset="A")
    tools = {t.name: t for t in photos_tools(home)}
    assert set(tools) == {"photos_find", "photos_events", "photos_look", "photos_note", "photos_originals", "photos_suggest_removing"}
    run = lambda name, args: asyncio.run(tools[name].handler(args))
    assert "Saved" in run("photos_note", {"id": "A", "note": "receipt from the pharmacy", "quality": 3})["content"][0]["text"]
    assert "receipt" in run("photos_find", {"query": "receipt"})["content"][0]["text"]
    assert any(c["type"] == "image" for c in run("photos_look", {"ids": ["A"]})["content"])


async def test_the_phone_app_shares_lists_sends_originals_and_removes(engine, aiohttp_client):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)

    def form(meta, frames=1):
        f = aiohttp.FormData()
        f.add_field("meta", json.dumps(meta), content_type="application/json")
        for _ in range(frames):
            f.add_field("frame", JPEG, filename="t.jpg", content_type="image/jpeg")
        return f

    r = await client.post("/api/photos", data=form({"id": "X/1", "kind": "video", "taken_at": T, "duration": 9}, 3))
    assert r.status == 200 and (await r.json())["frames"] == 3
    assert (await client.post("/api/photos", data=form({"kind": "photo"}))).status == 400  # no id
    pid = "X-1"
    assert (await (await client.get(f"/api/photos/{pid}/thumb?n=2")).read()) == JPEG

    # Ken looks at the new ones in the background.
    r = await (await client.post("/api/photos/look")).json()
    assert r == {"looking": True, "new": 1}
    assert any(run["title"] == "Looking at your photos" for run in engine.list_runs())

    # Ken asks for the full-size file; the phone sees the ask and sends it.
    app.photos.want_originals([pid])
    listed = await (await client.get("/api/photos")).json()
    assert listed["photos"][0]["id"] == pid and listed["originals"] == [{"id": pid, "asset_id": "X/1", "kind": "video"}]
    f = aiohttp.FormData()
    f.add_field("file", b"the whole video", filename="clip.MOV", content_type="video/quicktime")
    assert (await (await client.post(f"/api/photos/{pid}/original", data=f)).json()) == {"saved": "X-1.mov"}
    assert (await (await client.get("/api/photos")).json())["originals"] == []

    # A clean-up suggestion: kept, or deleted on the phone (Ken forgets it).
    app.photos.suggest_removing([{"id": pid, "why": "shaky"}])
    assert (await (await client.get("/api/photos")).json())["remove"][0]["why"] == "shaky"
    assert (await client.post(f"/api/photos/{pid}/keep")).status == 200
    assert (await (await client.get("/api/photos")).json())["remove"] == []
    assert (await client.delete(f"/api/photos/{pid}")).status == 200
    assert (await client.delete(f"/api/photos/{pid}")).status == 404
    assert (await (await client.get("/api/photos")).json())["photos"] == []
