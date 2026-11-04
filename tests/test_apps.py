"""Hosted Ken's apps: Ken asks its host what's connected (never guesses), hands out the host's one-tap Connect
link for the rest, and carries on in the chat that gave the link once the host says it's connected."""
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


async def test_settings_lists_connected_apps(engine, aiohttp_client, monkeypatch, on_host):  # noqa: F811
    monkeypatch.setattr(apps, "fetch", lambda app="": DATA)
    app, client = await signed_in(engine, aiohttp_client)
    data = await (await client.get("/api/apps")).json()
    assert data == {"connected": DATA["connected"], "apps_url": DATA["apps_url"]}

    def broken(app=""):
        raise OSError("down")

    monkeypatch.setattr(apps, "fetch", broken)
    r = await client.get("/api/apps")
    assert r.status == 502 and (await r.json())["error"] == "Couldn't check your apps just now."
