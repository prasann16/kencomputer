"""Hosted Ken's apps: Ken asks its host what's connected (never guesses), hands out the host's one-tap Connect
link for the rest, and carries on in the chat that gave the link once the host says it's connected."""
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apps  # noqa: E402
from engine import HOME  # noqa: E402
from test_hosted import engine, signed_in  # noqa: E402,F401

SITE = "https://app.example.dev"
DATA = {
    "connected": [{"slug": "gmail", "name": "Gmail", "accounts": ["sam@example.com"]}],
    "apps_url": f"{SITE}/account/apps",
    "match": {"slug": "notion", "name": "Notion", "connected": False, "connect_url": f"{SITE}/account/apps/connect/notion"},
}


@pytest.fixture
def on_host(monkeypatch):
    monkeypatch.setenv("KEN_KEEPALIVE_URL", f"{SITE}/box/checkin")
    monkeypatch.setenv("KEN_KEEPALIVE_TOKEN", "checkin-tok")


def test_ken_asks_the_host_with_its_checkin_token(on_host, monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen.update(url=req.full_url, auth=req.headers["Authorization"], timeout=timeout)
        return io.BytesIO(json.dumps(DATA).encode())

    monkeypatch.setattr(apps.urllib.request, "urlopen", urlopen)
    assert apps.fetch("notion")["match"]["name"] == "Notion"
    assert seen == {"url": f"{SITE}/box/apps?app=notion", "auth": "Bearer checkin-tok", "timeout": apps.TIMEOUT}


def test_what_ken_reads():
    text = apps.describe(DATA, "notion")
    assert "Connected apps: Gmail (gmail): sam@example.com" in text
    assert f"Notion isn't connected. Give them this one-tap link in one line, as is: {SITE}/account/apps/connect/notion" in text
    assert "is connected. Use the apps tools" in apps.describe({**DATA, "match": {**DATA["match"], "connected": True}}, "notion")
    assert 'No app called "zzz" is available' in apps.describe({**DATA, "match": None}, "zzz")
    assert "No apps are connected yet." in apps.describe({"connected": [], "apps_url": ""})


def test_a_host_that_doesnt_answer_is_said_plainly(on_host, monkeypatch, capsys):
    def urlopen(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(apps.urllib.request, "urlopen", urlopen)
    assert apps.main(["notion"]) == 1
    assert capsys.readouterr().out.startswith("Couldn't check their apps just now.")


def test_not_hosted(monkeypatch, capsys):
    monkeypatch.delenv("KEN_KEEPALIVE_URL", raising=False)
    assert apps.main([]) == 1 and not apps.hosted()


async def test_connected_carries_on_in_the_chat_that_gave_the_link(engine, monkeypatch):  # noqa: F811
    sent = []

    async def send_message(pid, text, *a, **kw):
        sent.append((pid, kw.get("prompt")))
        return True

    monkeypatch.setattr(engine, "send_message", send_message)
    engine.log_message(HOME, "you", "check my Notion for the launch notes")
    engine.log_message(HOME, "ken", f"Connect Notion: {SITE}/account/apps/connect/notion")
    assert await engine.app_connected("notion", "Notion") == HOME
    for task in engine._bg:
        await task
    assert sent == [(HOME, "(They've just connected Notion. Carry on with what they asked.)")]
    assert await engine.app_connected("slack", "Slack") is None  # no chat gave a Slack link


async def test_the_connected_endpoint(engine, aiohttp_client, monkeypatch, on_host):  # noqa: F811
    calls = []

    async def app_connected(slug, name):
        calls.append((slug, name))
        return HOME

    monkeypatch.setattr(engine, "app_connected", app_connected)
    app, client = await signed_in(engine, aiohttp_client)
    r = await client.post("/api/apps/connected", json={"slug": "notion", "name": "Notion"})
    assert r.status == 200 and (await r.json())["chat"] == HOME
    assert (await client.post("/api/apps/connected", json={"slug": "../x"})).status == 400
    assert calls == [("notion", "Notion")]
    monkeypatch.delenv("KEN_KEEPALIVE_URL")
    assert (await client.post("/api/apps/connected", json={"slug": "notion"})).status == 404


async def test_reconnect_after_the_move_is_one_note_with_a_link_per_app(engine, aiohttp_client, monkeypatch, on_host):  # noqa: F811
    seen = []
    q = engine.subscribe()
    app, client = await signed_in(engine, aiohttp_client)
    body = {"id": "reconnect-1763553600000", "apps": [
        {"slug": "gmail", "name": "Gmail", "url": f"{SITE}/account/apps/connect/gmail"},
        {"slug": "instagram", "name": "Insta[gram]", "url": f"{SITE}/account/apps/connect/instagram"},
    ]}
    assert (await client.post("/api/apps/reconnect", json=body)).status == 200
    note = engine.messages(HOME, limit=5)[-1]
    assert note["text"] == ("I've upgraded how I connect to your apps. Reconnect them once and everything works as before. "
                            f"Tap each one: [Gmail]({SITE}/account/apps/connect/gmail) · [Instagram]({SITE}/account/apps/connect/instagram)")
    # Started by Ken, so it goes to Telegram and the phone app like any message Ken starts (bot.py forwards "background").
    assert note["role"] == "ken" and note["via"] == "background" and note["action"] == "apps_reconnect"
    while not q.empty():
        seen.append(q.get_nowait())
    assert any(e["type"] == "message" and e.get("action") == "apps_reconnect" for e in seen)
    # The host asks again (its answer was lost): still one note.
    assert (await client.post("/api/apps/reconnect", json=body)).status == 200
    assert sum(m.get("action") == "apps_reconnect" for m in engine.messages(HOME, limit=50)) == 1
    # Only the host's own Connect links, for the app named.
    for bad in ({**body, "apps": [{"slug": "gmail", "name": "Gmail", "url": "https://evil.example/x"}]},
                {**body, "apps": [{"slug": "gmail", "name": "Gmail", "url": f"{SITE}/account/apps/connect/notion"}]},
                {**body, "apps": []}, {**body, "id": "../x"}, {"apps": body["apps"]}):
        assert (await client.post("/api/apps/reconnect", json=bad)).status == 400
    monkeypatch.delenv("KEN_KEEPALIVE_URL")
    assert (await client.post("/api/apps/reconnect", json=body)).status == 404


async def test_connecting_from_the_reconnect_note_doesnt_start_a_turn(engine, monkeypatch):  # noqa: F811
    sent = []

    async def send_message(pid, text, *a, **kw):
        sent.append(pid)
        return True

    monkeypatch.setattr(engine, "send_message", send_message)
    engine.apps_reconnect("reconnect-1", [{"slug": "gmail", "name": "Gmail", "url": f"{SITE}/account/apps/connect/gmail"}])
    assert await engine.app_connected("gmail", "Gmail") is None
    assert sent == []


async def test_settings_lists_connected_apps(engine, aiohttp_client, monkeypatch, on_host):  # noqa: F811
    monkeypatch.setattr(apps, "fetch", lambda app="": DATA)
    app, client = await signed_in(engine, aiohttp_client)
    data = await (await client.get("/api/apps")).json()
    assert data == {"connected": [{"slug": "gmail", "name": "Gmail", "accounts": []}], "apps_url": DATA["apps_url"]}  # an older host: no account list

    def broken(app=""):
        raise OSError("down")

    monkeypatch.setattr(apps, "fetch", broken)
    r = await client.get("/api/apps")
    assert r.status == 502 and (await r.json())["error"] == "Couldn't check your apps just now."


async def test_credits_running_low_is_one_note_with_the_three_ways_on(engine, aiohttp_client, on_host):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    assert (await client.post("/api/credits/low", json={"used_pct": 80})).status == 200
    note = engine.messages(HOME, limit=5)[-1]
    assert note["role"] == "ken" and note["action"] == "credits" and note["note"] is True and not note.get("error")
    assert note["text"].startswith("You've used 80% of your Ken AI credits.")
    assert (await client.post("/api/credits/low", data="x")).status == 400


async def test_credits_note_is_only_for_ken_in_the_cloud(engine, aiohttp_client, monkeypatch, on_host):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    monkeypatch.delenv("KEN_KEEPALIVE_URL")
    assert (await client.post("/api/credits/low", json={"used_pct": 80})).status == 404


# ------------------------------------------------------------------ more than one account of an app

NOV5 = 1793900000000  # 2026-11-05, ms


class AppsServer:
    """A stand-in for the apps MCP server, shaped like Agent37's managed Composio (design-shots/round21): accounts
    listed newest first under their own word ids, `account` on each execute item, and a call that leaves `account`
    out when two are connected refused, as the tool's own description says it must be."""

    def __init__(self, accounts):
        self.accounts = accounts  # {slug: [{"id", "email", "alias"?}]}, newest first
        self.calls = []

    def answer(self, tool, args):
        if tool == "COMPOSIO_MANAGE_CONNECTIONS":
            out = {}
            for t in args["toolkits"]:
                if t["action"] == "list":
                    out[t["name"]] = {"accounts": [{"id": a["id"], "status": "active", "is_default": i == 0, **({"alias": a["alias"]} if a.get("alias") else {})}
                                                   for i, a in enumerate(self.accounts.get(t["name"], []))]}
                elif t["action"] == "remove":
                    self.accounts[t["name"]] = [a for a in self.accounts[t["name"]] if a["id"] != t["account_id"]]
                    out[t["name"]] = {"removed": t["account_id"]}
            return {"successful": True, "data": out}
        if tool == "COMPOSIO_MULTI_EXECUTE_TOOL":
            item = args["tools"][0]
            slug = item["tool_slug"].split("_")[0].lower()
            mine = self.accounts.get(slug, [])
            if len(mine) > 1 and "account" not in item:
                return {"isError": True, "text": "When multiple accounts are connected, you MUST specify which account to use."}
            hit = next((a for a in mine if a["id"] == item.get("account") or a.get("alias") == item.get("account")), None)
            if not hit:
                return {"isError": True, "text": "No active connection found"}
            return {"successful": True, "data": {"results": [{"response": {"data": {"emailAddress": hit["email"], "messagesTotal": 12}}}]}}
        raise AssertionError(tool)

    def urlopen(self, req, timeout):
        if "/box/apps" in req.full_url:
            return io.BytesIO(json.dumps(self.host).encode())
        assert req.headers["Authorization"] == "Bearer mcp-tok" and "text/event-stream" in req.headers["Accept"]
        body = json.loads(req.data)
        tool, args = body["params"]["name"], body["params"]["arguments"]
        self.calls.append((tool, args))
        a = self.answer(tool, args)
        result = {"content": [{"type": "text", "text": a["text"]}], "isError": True} if a.get("isError") else {"content": [{"type": "text", "text": json.dumps(a)}]}
        # The server answers with server-sent events.
        return io.BytesIO(f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result})}\n\n".encode())


@pytest.fixture
def two_gmails(on_host, monkeypatch, tmp_path):
    monkeypatch.setenv("KEN_HOME", str(tmp_path))
    monkeypatch.setenv("KEN_APPS_MCP_URL", "https://apps.example/mcp")
    monkeypatch.setenv("KEN_APPS_MCP_TOKEN", "mcp-tok")
    server = AppsServer({"gmail": [{"id": "gmail_adieu-ethics", "email": "sam@work.example"}, {"id": "gmail_jati-off", "email": "sam@home.example"}]})
    server.host = {"connected": [{"slug": "gmail", "name": "Gmail", "accounts": [], "account_list": [
        {"id": "ca_new", "connected_at": NOV5 + 3600_000, "label": None}, {"id": "ca_old", "connected_at": NOV5, "label": None}]}],
        "apps_url": f"{SITE}/account/apps", "match": None}
    monkeypatch.setattr(apps.urllib.request, "urlopen", server.urlopen)
    return server


def test_each_account_is_named_with_that_account_once(two_gmails):
    got = apps.with_accounts(two_gmails.host["connected"])
    assert got == [{"slug": "gmail", "name": "Gmail", "accounts": [
        {"id": "gmail_jati-off", "name": "sam@home.example", "label": "sam@home.example", "default": False, "connected_at": NOV5, "via": "apps"},
        {"id": "gmail_adieu-ethics", "name": "sam@work.example", "label": "sam@work.example", "default": True, "connected_at": NOV5 + 3600_000, "via": "apps"},
    ]}]
    profiles = [args["tools"][0] for tool, args in two_gmails.calls if tool == "COMPOSIO_MULTI_EXECUTE_TOOL"]
    assert profiles == [{"tool_slug": "GMAIL_GET_PROFILE", "arguments": {}, "account": "gmail_jati-off"},
                        {"tool_slug": "GMAIL_GET_PROFILE", "arguments": {}, "account": "gmail_adieu-ethics"}]
    # Kept: the next look asks the list again, but no profile (each is an app action).
    two_gmails.calls.clear()
    assert apps.with_accounts(two_gmails.host["connected"]) == got
    assert [tool for tool, _ in two_gmails.calls] == ["COMPOSIO_MANAGE_CONNECTIONS"]


def test_an_alias_wins_and_an_unnameable_account_is_numbered_with_its_date(two_gmails):
    two_gmails.accounts["gmail"][0]["alias"] = "work"
    two_gmails.accounts["gmail"][1]["email"] = None  # the app wouldn't say
    got = apps.with_accounts(two_gmails.host["connected"])[0]["accounts"]
    assert [a["label"] for a in got] == ["Account 1 · connected Nov 5", "work"]
    assert not any(args["tools"][0].get("account") == "gmail_adieu-ethics" for tool, args in two_gmails.calls if tool == "COMPOSIO_MULTI_EXECUTE_TOOL")
    # Asked again only after a week.
    two_gmails.calls.clear()
    apps.with_accounts(two_gmails.host["connected"])
    assert [tool for tool, _ in two_gmails.calls] == ["COMPOSIO_MANAGE_CONNECTIONS"]
    apps.with_accounts(two_gmails.host["connected"], now=apps.time.time() + apps.RETRY_UNNAMED_S + 60)
    assert [tool for tool, _ in two_gmails.calls][-1] == "COMPOSIO_MULTI_EXECUTE_TOOL"


def test_dates_are_borrowed_only_when_the_two_lists_agree(two_gmails):
    two_gmails.accounts["gmail"].append({"id": "gmail_third", "email": None})
    accounts = apps.with_accounts(two_gmails.host["connected"])[0]["accounts"]
    assert [a["connected_at"] for a in accounts] == [None, None, None]
    assert accounts[0]["label"] == "Account 1"


def test_when_the_apps_tools_cant_be_asked_the_hosts_list_stands_in(two_gmails, monkeypatch):
    monkeypatch.setattr(apps, "mcp_accounts", lambda slugs: (_ for _ in ()).throw(TimeoutError()))
    accounts = apps.with_accounts(two_gmails.host["connected"])[0]["accounts"]
    assert [(a["id"], a["label"], a["via"]) for a in accounts] == [("ca_old", "Account 1 · connected Nov 5", "host"), ("ca_new", "Account 2 · connected Nov 5", "host")]


def test_ken_apps_lists_accounts_with_the_id_to_pass(two_gmails, capsys):
    assert apps.main([]) == 0
    out = capsys.readouterr().out
    assert ("Connected apps: Gmail (gmail): 2 accounts: sam@home.example [account: gmail_jati-off]; "
            "sam@work.example [account: gmail_adieu-ethics, the default]") in out
    assert "pass `account` (the id in brackets) on every apps call" in out and "ask them once" in out
    # One account: just its name, no instruction.
    two_gmails.accounts["gmail"].pop()
    two_gmails.host["connected"][0]["account_list"].pop()
    apps.main([])
    out = capsys.readouterr().out
    assert "Gmail (gmail): sam@work.example\n" in out and "pass `account`" not in out


def test_the_instructions_say_always_pass_account():
    import bot
    for prompt in (bot.HOSTED_APPS_PROMPT, bot.APPS_PROMPT):
        assert "always pass `account`" in prompt and "my work Gmail" in prompt and "ask them once" in prompt
    assert "--apps` lists each app's accounts" in bot.HOSTED_APPS_PROMPT


async def test_settings_shows_named_accounts_and_disconnects_one(engine, aiohttp_client, two_gmails):  # noqa: F811
    app, client = await signed_in(engine, aiohttp_client)
    data = await (await client.get("/api/apps")).json()
    assert [a["label"] for a in data["connected"][0]["accounts"]] == ["sam@home.example", "sam@work.example"]
    r = await client.post("/api/apps/disconnect", json={"app": "gmail", "account": "gmail_jati-off"})
    assert r.status == 200
    assert ("COMPOSIO_MANAGE_CONNECTIONS", {"toolkits": [{"name": "gmail", "action": "remove", "account_id": "gmail_jati-off"}]}) in two_gmails.calls
    assert [a["id"] for a in two_gmails.accounts["gmail"]] == ["gmail_adieu-ethics"]
    assert "gmail:gmail_jati-off" not in json.loads((Path(os.environ["KEN_HOME"]) / "app-accounts.json").read_text())
    assert (await client.post("/api/apps/disconnect", json={"app": "gmail", "account": "../x"})).status == 400


async def test_a_disconnect_that_didnt_take_says_so(engine, aiohttp_client, two_gmails, monkeypatch):  # noqa: F811
    real = two_gmails.answer
    monkeypatch.setattr(two_gmails, "answer", lambda tool, args: {"successful": True, "data": {}} if args.get("toolkits", [{}])[0].get("action") == "remove" else real(tool, args))
    app, client = await signed_in(engine, aiohttp_client)
    r = await client.post("/api/apps/disconnect", json={"app": "gmail", "account": "gmail_jati-off"})
    assert r.status == 502 and "Couldn't disconnect" in (await r.json())["error"]


# ------------------------------------------------------------------ files, on a host that runs the apps tools itself

@pytest.fixture
def own_apps(on_host, monkeypatch):
    monkeypatch.setenv("KEN_APPS_MCP_URL", f"{SITE}/box/apps-mcp")
    monkeypatch.setenv("KEN_APPS_MCP_TOKEN", "checkin-tok")


def test_a_file_goes_to_composio_through_the_host_and_ken_gets_the_tool_argument(own_apps, monkeypatch, tmp_path, capsys):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"not really a video")
    staged = {"upload_url": "https://storage.composio.dev/put?sig=1", "upload_headers": {"content-type": "video/mp4"},
              "file": {"name": "reel.mp4", "mimetype": "video/mp4", "s3key": "projects/p/reel.mp4"}}
    seen = []

    def urlopen(req, timeout):
        body = req.data.read() if hasattr(req.data, "read") else req.data
        seen.append({"url": req.full_url, "method": req.get_method(), "headers": {k.lower(): v for k, v in req.header_items()}, "body": body})
        return io.BytesIO(json.dumps(staged).encode() if req.full_url.startswith(SITE) else b"")

    monkeypatch.setattr(apps.urllib.request, "urlopen", urlopen)
    assert apps.file_main([str(video), "youtube_upload_video"]) == 0
    ask, put = seen
    assert ask["url"] == f"{SITE}/box/apps-file" and ask["headers"]["authorization"] == "Bearer checkin-tok"
    assert json.loads(ask["body"]) == {"tool": "YOUTUBE_UPLOAD_VIDEO", "app": "youtube", "name": "reel.mp4", "mimetype": "video/mp4",
                                       "md5": hashlib.md5(b"not really a video").hexdigest(), "size": 18}
    # The file itself goes straight to Composio's storage, never through the host, with no token of ours.
    assert put["url"] == staged["upload_url"] and put["method"] == "PUT" and put["body"] == b"not really a video"
    assert put["headers"]["content-type"] == "video/mp4" and "authorization" not in put["headers"]
    assert put["headers"]["user-agent"] == "Ken"  # Cloudflare in front of storage may refuse Python's own
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[0]) == staged["file"]
    assert "as the file argument of YOUTUBE_UPLOAD_VIDEO" in out


def test_files_plainly_refused_where_the_host_cant_take_them(on_host, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("KEN_APPS_MCP_URL", "https://api.agent37.com/mcp/composio")  # Agent37's apps server
    assert not apps.files_ready()
    assert apps.file_main([str(tmp_path / "a.png"), "TWITTER_UPLOAD_MEDIA"]) == 1
    assert "can't carry this file for now" in capsys.readouterr().out


def test_a_missing_file_or_a_refusal_is_said_plainly(own_apps, monkeypatch, capsys, tmp_path):
    assert apps.file_main([str(tmp_path / "nope.png"), "TWITTER_UPLOAD_MEDIA"]) == 1
    assert "There's no file at" in capsys.readouterr().out
    (tmp_path / "a.png").write_bytes(b"png")

    def urlopen(req, timeout):
        raise apps.urllib.error.HTTPError(req.full_url, 409, "no", {}, io.BytesIO(b'{"error": "Apps on this computer go through Agent37."}'))

    monkeypatch.setattr(apps.urllib.request, "urlopen", urlopen)
    assert apps.file_main([str(tmp_path / "a.png"), "TWITTER_UPLOAD_MEDIA"]) == 1
    assert "go through Agent37" in capsys.readouterr().out


def test_the_file_line_is_in_kens_instructions_only_where_files_work(own_apps, monkeypatch):
    import bot
    assert bot.apps_mcp()["apps"]["url"] == f"{SITE}/box/apps-mcp"
    assert "--apps-file <path> <TOOL_SLUG>" in bot.build_system()
    monkeypatch.setenv("KEN_APPS_MCP_URL", "https://api.agent37.com/mcp/composio")
    assert "--apps-file" not in bot.build_system()
