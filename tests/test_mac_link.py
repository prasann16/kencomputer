"""Ken for Mac linked to their Ken in the cloud (mac_link.py): linking with a code, the outbound-only poll loop,
switches that are off until they turn them on, folders nothing outside of which is reachable, and the cloud Ken's
`ken --mac` commands. Never talks to the real site: every request goes to a fake (httpx.MockTransport)."""
import asyncio
import base64
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mac_link  # noqa: E402
from mac_link import MacLink  # noqa: E402

SITE = "https://site.test"


def fake_site(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def link(tmp_path):
    return MacLink(tmp_path / "home", site=SITE, name=lambda: "Sam's MacBook")


@pytest.fixture(autouse=True)
def home(tmp_path):
    (tmp_path / "home").mkdir()


async def linked(link, folders=(), files=False):
    def site(request):
        assert request.url.path == "/mac/link"
        assert json.loads(request.content) == {"code": "ABCD2345", "name": "Sam's MacBook"}
        return httpx.Response(200, json={"token": "t" * 64, "account": "sam@example.com"})
    await link.link("abcd-2345", client=fake_site(site))
    if folders:
        link.set_folders([str(f) for f in folders])
    if files:
        link.set_switch("files", True)


# ------------------------------------------------------------------ linking

async def test_a_code_links_and_the_token_is_only_readable_by_them(link):
    assert link.status()["linked"] is False
    await linked(link)
    st = link.status()
    assert st["linked"] and st["account"] == "sam@example.com"
    assert [s["on"] for s in st["switches"]] == [False, False]  # everything off until they turn it on
    assert stat.S_IMODE(link.file.stat().st_mode) == 0o600
    assert "t" * 64 not in json.dumps(st)  # the screen never gets the token


async def test_a_bad_code_says_what_to_do(link):
    bad = fake_site(lambda r: httpx.Response(400, json={"error": "That code didn't work. Get a new one."}))
    with pytest.raises(ValueError, match="Get a new one"):
        await link.link("ABCD-2345", client=bad)
    with pytest.raises(ValueError, match="Settings → Your Mac"):
        await link.link("ab")
    assert not link.linked()


async def test_unlink_tells_the_site_and_forgets_the_token(link):
    await linked(link)
    seen = []
    await link.unlink(client=fake_site(lambda r: seen.append((r.url.path, r.headers["authorization"])) or httpx.Response(200, json={"ok": True})))
    assert seen == [("/mac/unlink", "Bearer " + "t" * 64)]
    assert not link.linked() and "token" not in link.file.read_text()


async def test_unlinking_turns_every_switch_off_so_a_new_link_starts_from_nothing(link, tmp_path):
    await linked(link, [tmp_path], files=True)
    await link.unlink(client=fake_site(lambda r: httpx.Response(200, json={"ok": True})))
    await linked(link)
    assert link.caps_on() == [] and link.state()["folders"] == [str(tmp_path)]


async def test_an_odd_answer_from_the_site_doesnt_stop_it(link, monkeypatch):
    await linked(link)
    naps = []

    async def nap(s):
        naps.append(s)
        raise asyncio.CancelledError

    monkeypatch.setattr(mac_link.asyncio, "sleep", nap)
    with pytest.raises(asyncio.CancelledError):
        await link.run(client=fake_site(lambda r: httpx.Response(200, json=["not", "a", "dict"])))
    assert naps == [5] and link.linked()


async def test_the_poll_answers_the_cloud_then_stops_when_unlinked_on_the_site(link, tmp_path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "notes.txt").write_text("hi")
    await linked(link, [docs], files=True)
    polls, answers = [], []

    async def site(request):
        body = json.loads(request.content)
        if request.url.path == "/mac/poll":
            polls.append(body)
            if len(polls) == 1:
                return httpx.Response(200, json={"calls": [{"id": "c1", "cap": "files.list", "args": {"path": str(docs)}}]})
            while not answers:  # the next poll holds, as the site's does, until the answer is in
                await asyncio.sleep(0.01)
            return httpx.Response(401, json={"error": "unlinked"})
        answers.append(body)
        return httpx.Response(200, json={"ok": True})

    task = asyncio.create_task(link.run(client=fake_site(site)))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if answers and not link.linked():
            break
    task.cancel()
    assert polls[0] == {"caps": ["files"]}
    assert answers[0]["id"] == "c1" and answers[0]["ok"] is True
    assert [i["name"] for i in answers[0]["result"]["items"]] == ["notes.txt"]
    assert not link.linked() and "unlinked" in link.status()["problem"]


async def test_off_the_internet_it_keeps_trying(link, monkeypatch):
    await linked(link)
    naps = []

    async def nap(s):
        naps.append(s)
        if len(naps) >= 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(mac_link.asyncio, "sleep", nap)
    with pytest.raises(asyncio.CancelledError):
        await link.run(client=fake_site(lambda r: (_ for _ in ()).throw(httpx.ConnectError("offline"))))
    assert naps == [5, 10, 20, 40]
    assert link.linked() and "Can't reach" in link.status()["problem"]


# ------------------------------------------------------------------ what the cloud may use

async def test_everything_is_off_until_they_switch_it_on(link, tmp_path):
    await linked(link, [tmp_path])
    r = await link.handle({"cap": "files.list", "args": {"path": str(tmp_path)}})
    assert r["ok"] is False and "is off in Ken for Mac" in r["error"]
    assert (await link.handle({"cap": "shell.run", "args": {}}))["ok"] is False


async def test_only_inside_the_folders_they_chose(link, tmp_path):
    shared, private = tmp_path / "Shared", tmp_path / "Private"
    shared.mkdir()
    private.mkdir()
    (private / "secret.txt").write_text("no")
    (shared / "ok.txt").write_text("yes")
    (shared / "sneaky").symlink_to(private / "secret.txt")
    await linked(link, [shared], files=True)
    get = lambda p: link.handle({"cap": "files.get", "args": {"path": str(p)}})
    ok = await get(shared / "ok.txt")
    assert ok["ok"] and base64.b64decode(ok["result"]["data"]) == b"yes"
    for path in (private / "secret.txt", shared / ".." / "Private" / "secret.txt", shared / "sneaky", "/etc/hosts"):
        r = await get(path)
        assert r == {"ok": False, "error": "That isn't in a folder they've shared. They choose folders in Ken for Mac → Settings → Cloud."}, path
    assert "no " in (await get(shared / "missing.txt"))["error"].lower()


async def test_find_and_list_skip_hidden_files_and_big_files_are_refused(link, tmp_path, monkeypatch):
    docs = tmp_path / "Docs"
    (docs / "Taxes").mkdir(parents=True)
    (docs / "Taxes" / "tax return 2025.pdf").write_bytes(b"%PDF")
    (docs / ".hidden tax.txt").write_text("x")
    (docs / "big.bin").write_bytes(b"0" * 20)
    await linked(link, [docs], files=True)
    found = (await link.handle({"cap": "files.find", "args": {"words": "Tax 2025"}}))["result"]["found"]
    assert [f["name"] for f in found] == ["tax return 2025.pdf"]
    items = (await link.handle({"cap": "files.list", "args": {"path": str(docs)}}))["result"]["items"]
    assert [(i["name"], i["kind"]) for i in items] == [("big.bin", "file"), ("Taxes", "folder")]
    monkeypatch.setattr(mac_link, "MAX_FILE", 10)
    assert "too big" in (await link.handle({"cap": "files.get", "args": {"path": str(docs / "big.bin")}}))["error"]


async def test_anything_that_changes_something_needs_their_ok(link, tmp_path):
    await linked(link, files=True)
    done = []
    link.caps["files.trash"] = mac_link.Cap("files", lambda a: done.append(a) or "gone", changes=True)
    assert (await link.handle({"cap": "files.trash", "args": {}})) == {"ok": False, "error": "They didn't allow that on their Mac."}
    asked = []

    async def ask(what, detail):
        asked.append(what)
        return True
    link.ask = ask
    assert (await link.handle({"cap": "files.trash", "args": {"path": "x"}}))["ok"] is True
    assert asked == ["files.trash"] and done == [{"path": "x"}]


def test_folders_must_be_real_and_not_the_whole_home(link, tmp_path):
    with pytest.raises(ValueError, match="isn't a folder"):
        link.set_folders([str(tmp_path / "nope")])
    with pytest.raises(ValueError, match="not all of it"):
        link.set_folders([str(Path.home())])


# ------------------------------------------------------------------ the cloud Ken: `ken --mac …`

def test_cloud_commands_ask_the_mac_and_save_files(monkeypatch, tmp_path, capsys):
    asked = []

    def ask(cap, args=None, timeout=60):
        asked.append((cap, args))
        if cap == "files.get":
            return {"name": "../../evil name.txt", "data": base64.b64encode(b"hello").decode()}
        if cap == "files.find":
            raise mac_link.Refused("Sam's MacBook is off, asleep or offline right now.")
        return {"folders": [{"name": "Docs", "path": "/Users/sam/Docs"}]}

    monkeypatch.setattr(mac_link, "ask_mac", ask)
    assert mac_link.cloud_main(["folders"]) == 0
    assert mac_link.cloud_main(["get", "/Users/sam/Docs/a b.txt", "--to", str(tmp_path)]) == 0
    assert (tmp_path / "evil name.txt").read_bytes() == b"hello"  # only ever the name, never a path from the Mac
    assert mac_link.cloud_main(["find", "tax"]) == 1
    out = capsys.readouterr().out
    assert "Docs: /Users/sam/Docs" in out and "offline" in out
    assert asked[1] == ("files.get", {"path": "/Users/sam/Docs/a b.txt"})


def test_off_the_cloud_there_is_no_mac_to_ask(monkeypatch):
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)
    with pytest.raises(mac_link.Refused, match="only be reached from Ken in the cloud"):
        mac_link.ask_mac("files.folders")


def test_the_cloud_ken_is_told_only_what_is_switched_on(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "LINKED_MAC", None)
    assert bot.mac_prompt() == ""
    monkeypatch.setattr(bot, "LINKED_MAC", {"name": "Sam's MacBook", "caps": [], "online": True})
    assert "haven't switched anything on" in bot.mac_prompt()
    monkeypatch.setattr(bot, "LINKED_MAC", {"name": "Sam's MacBook", "caps": ["files"], "online": False})
    prompt = bot.mac_prompt()
    assert "Sam's MacBook" in prompt and "--mac find" in prompt


# ------------------------------------------------------------------ Settings → Cloud on the Mac

async def test_settings_on_the_mac(tmp_path, aiohttp_client, link):
    from test_engine import FakeBrain
    from engine import Engine
    from web import WebApp
    e = Engine(tmp_path, FakeBrain())
    app = WebApp(e, tmp_path, link=link)
    client = await aiohttp_client(app.build())
    auth = {"Authorization": f"Bearer {app.token}"}
    assert (await (await client.get("/api/link", headers=auth)).json())["linked"] is False
    r = await client.post("/api/link", json={"action": "link", "code": "x"}, headers=auth)
    assert r.status == 400 and "Settings → Your Mac" in (await r.json())["error"]
    assert (await client.post("/api/link", json={"action": "switch", "id": "files", "on": True}, headers=auth)).status == 200
    r = await client.post("/api/link", json={"action": "folders", "folders": [str(tmp_path)]}, headers=auth)
    assert (await r.json())["folders"] == [str(tmp_path)]
    assert (await client.get("/api/link")).status == 401  # only Ken's own screens
