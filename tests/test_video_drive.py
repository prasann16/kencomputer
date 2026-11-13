"""Hosted Ken keeps edited videos in the person's Google Drive: it asks for Drive before an edit, Drive fetches each
file through a one-file link, the copy is checked byte for byte, and only then is the file deleted here."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apps  # noqa: E402
import drive_store  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KEN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def project(home):
    folder = home / "work" / "videos" / "talk"
    folder.mkdir(parents=True)
    (folder / "source.mov").write_bytes(os.urandom(3 * 1024 * 1024 + 7))
    (folder / "source.words.json").write_text("[]")
    (folder / "cut.mp4").write_bytes(os.urandom(2048))
    (folder / "reel.mp4").write_bytes(os.urandom(1024 * 1024 + 3))
    (folder / "props.json").write_text(json.dumps({"video": "cut.mp4", "cuts": [[0, 4.2]]}))
    return folder


def test_a_link_opens_one_file_for_half_an_hour(home, project):
    token = drive_store.open_link(project / "reel.mp4", now=1000)
    assert drive_store.linked_file(token, now=1000 + 60) == (project / "reel.mp4").resolve()
    assert drive_store.linked_file(token, now=1000 + drive_store.LINK_TTL_S + 1) is None
    assert drive_store.linked_file("not-a-token", now=1000) is None
    drive_store.close_link(token)
    assert drive_store.linked_file(token, now=1000) is None
    assert oct((home / "drive-out.json").stat().st_mode)[-3:] == "600"


async def test_web_app_serves_a_linked_file_only_with_its_token(engine, aiohttp_client, home, project):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    token = drive_store.open_link(project / "reel.mp4")
    r = await client.get(f"/api/drive-file/{token}")
    assert r.status == 200 and await r.read() == (project / "reel.mp4").read_bytes()
    assert (await client.get("/api/drive-file/wrong")).status == 404
    drive_store.close_link(token)
    assert (await client.get(f"/api/drive-file/{token}")).status == 404
    client.session.cookie_jar.clear()  # the host's keys are needed too: a token alone opens nothing
    token = drive_store.open_link(project / "reel.mp4")
    assert (await client.get(f"/api/drive-file/{token}")).status == 401


async def test_save_puts_reel_and_original_in_drive_then_deletes_them(engine, aiohttp_client, home, project, tmp_path, monkeypatch):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    (home / "web-token").write_text(app.token)
    monkeypatch.setenv("KEN_DRIVE_FAKE", str(tmp_path / "drive"))
    monkeypatch.setenv("KEN_DRIVE_FILE_BASE", f"http://{client.server.host}:{client.server.port}")
    reel, src = (project / "reel.mp4").read_bytes(), (project / "source.mov").read_bytes()
    said = []
    assert await asyncio.to_thread(drive_store.save, project, True, "", said.append) == 0  # Drive fetches from this server
    assert not (project / "reel.mp4").exists() and not (project / "source.mov").exists() and not (project / "cut.mp4").exists()
    assert (project / "props.json").exists() and (project / "source.words.json").exists()
    note = json.loads((project / "drive.json").read_text())
    assert (tmp_path / "drive" / f"{note['reel']['id']}.bin").read_bytes() == reel
    assert (tmp_path / "drive" / f"{note['source']['id']}.bin").read_bytes() == src
    assert note["reel"]["link"].startswith("https://drive.google.com/file/d/")
    assert any(note["reel"]["link"] in s for s in said)
    assert len((home / "drive-videos.jsonl").read_text().splitlines()) == 2
    assert drive_store._links() == {}  # links closed once Drive has the file
    # One "Ken videos" folder, reused by the next save
    folders = [p for p in (tmp_path / "drive").glob("fld_*.json")]
    assert len(folders) == 1
    # A re-edit: the original comes back, checked
    assert await asyncio.to_thread(drive_store.fetch, project, "", said.append) == 0
    assert (project / "source.mov").read_bytes() == src


def test_a_copy_that_doesnt_match_keeps_the_file(home, project, monkeypatch):
    calls = []

    def fake_call(tool, args, account=""):
        calls.append(tool)
        if tool == "GOOGLEDRIVE_FIND_FILE":
            return {"data": {"files": [{"id": "fld1"}]}}
        if tool == "GOOGLEDRIVE_UPLOAD_FROM_URL":
            return {"data": {"id": "f1"}}
        if tool == "GOOGLEDRIVE_GET_FILE_METADATA":
            return {"data": {"id": "f1", "size": "12", "md5Checksum": "0" * 32}}  # truncated upload
    monkeypatch.setattr(drive_store, "call", fake_call)
    said = []
    assert drive_store.save(project, True, "", said.append) == 1
    assert (project / "reel.mp4").exists() and (project / "source.mov").exists() and (project / "cut.mp4").exists()
    assert "wasn't saved" in said[-1] and "don't delete" in said[-1]
    assert drive_store._links() == {}


def test_drive_through_the_apps_tools(home, project, monkeypatch):
    """The real path: COMPOSIO_MULTI_EXECUTE_TOOL with GOOGLEDRIVE_UPLOAD_FROM_URL and the host's public link."""
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://app.example.dev/box/checkin")
    monkeypatch.setenv("KEN_INSTANCE_ID", "abc123")
    sent = []
    reel = project / "reel.mp4"

    def mcp_call(tool, arguments):
        item = arguments["tools"][0]
        sent.append(item)
        slug, a = item["tool_slug"], item["arguments"]
        if slug == "GOOGLEDRIVE_FIND_FILE":
            return {"data": {"results": [{"response": {"data": {"files": []}, "successful": True}}]}}
        if slug == "GOOGLEDRIVE_CREATE_FOLDER":
            return {"data": {"results": [{"response": {"data": {"id": "fldX"}, "successful": True}}]}}
        if slug == "GOOGLEDRIVE_UPLOAD_FROM_URL":
            assert a["source_url"].startswith("https://app.example.dev/box/drive-file/abc123/")
            token = a["source_url"].rsplit("/", 1)[1]
            assert drive_store.linked_file(token) == reel.resolve()  # open while Drive fetches
            assert a["parent_folder_id"] == "fldX" and a["mime_type"] == "video/mp4" and "source_headers" not in a
            return {"data": {"results": [{"response": {"data": {"id": "F9"}, "successful": True}}]}}
        if slug == "GOOGLEDRIVE_GET_FILE_METADATA":
            return {"data": {"results": [{"response": {"data": {"id": "F9", "size": str(reel.stat().st_size),
                    "md5Checksum": drive_store.md5(reel), "webViewLink": "https://drive.google.com/file/d/F9/view"}, "successful": True}}]}}
        raise AssertionError(slug)
    monkeypatch.setattr(apps, "mcp_call", mcp_call)
    said = []
    assert drive_store.save(project, False, "acct_2", said.append) == 0
    assert all(i.get("account") == "acct_2" for i in sent)
    assert not reel.exists() and (project / "source.mov").exists() and (project / "cut.mp4").exists()  # --reel-only keeps the rest
    assert "https://drive.google.com/file/d/F9/view" in said[0]


def test_a_failed_drive_call_is_an_error(home, monkeypatch):
    monkeypatch.setattr(apps, "mcp_call", lambda tool, a: {"data": {"results": [{"response": {"successful": False, "error": "insufficient scope"}}]}})
    with pytest.raises(drive_store.DriveError, match="insufficient scope"):
        drive_store.call("GOOGLEDRIVE_FIND_FILE", {})


def test_check_gives_the_connect_link_when_drive_isnt_connected(home, monkeypatch, capsys):
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://app.example.dev/box/checkin")
    monkeypatch.setenv("KEN_KEEPALIVE_TOKEN", "t")
    monkeypatch.setenv("KEN_APPS_MCP_URL", "https://mcp.example.dev/x")
    monkeypatch.setenv("KEN_APPS_MCP_TOKEN", "m")
    asked = []
    def fetch(app=""):
        asked.append(app)
        return {"connected": [], "apps_url": "https://app.example.dev/account/apps",
                "match": {"slug": "googledrive", "name": "Google Drive", "connected": False,
                          "connect_url": "https://app.example.dev/account/apps/connect/googledrive"}}
    monkeypatch.setattr(apps, "fetch", fetch)
    assert drive_store.main(["check"]) == 0
    out = capsys.readouterr().out
    assert asked == ["Google Drive"]
    assert "https://app.example.dev/account/apps/connect/googledrive" in out and "stop" in out
    monkeypatch.setattr(apps, "fetch", lambda app="": {"connected": [{"slug": "googledrive", "name": "Google Drive", "accounts": ["a"]}],
                                                       "match": {"slug": "googledrive", "connected": True}})
    assert drive_store.main(["check"]) == 0
    assert "is connected" in capsys.readouterr().out


def test_on_a_personal_computer_videos_stay_put(home, monkeypatch, capsys):
    for k in ("KEN_KEEPALIVE_URL", "KEN_APPS_MCP_URL", "KEN_DRIVE_FAKE"):
        monkeypatch.delenv(k, raising=False)
    assert drive_store.main(["check"]) == 1
    assert "Ken in the cloud" in capsys.readouterr().out


def test_hosted_ken_is_told_to_ask_for_drive_first(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "apps_mcp", lambda: {"apps": {}})
    monkeypatch.setattr(bot, "host_site", lambda: "https://app.example.dev")
    monkeypatch.setattr(bot, "video_prompt", lambda: "VIDEO")
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://app.example.dev/box/checkin")
    monkeypatch.setenv("KEN_KEEPALIVE_TOKEN", "t")
    system = bot.build_system()
    assert "--video-drive check" in system and "--video-drive save" in system
    monkeypatch.setattr(bot, "host_site", lambda: "")
    assert "--video-drive" not in bot.build_system()
