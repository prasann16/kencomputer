"""Hosted Ken in the phone app: replies, approvals and finished work reach the app's server for a push."""
import asyncio
import os
import tempfile

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
import bot  # noqa: E402


class FakeEngine:
    def __init__(self):
        self.q = asyncio.Queue()

    def subscribe(self):
        return self.q

    def get_agent(self, pid):
        return {"name": "Researcher"}


class FakeClient:
    sent = []

    def __init__(self, timeout):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json, headers):
        FakeClient.sent.append((url, json, headers))


async def test_what_buzzes_reaches_the_app(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(bot, "ENGINE", engine)
    monkeypatch.setattr(bot.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("KEN_NOTIFY_URL", "https://example.test/app/notify")
    monkeypatch.setenv("KEN_NOTIFY_TOKEN", "t0k")
    FakeClient.sent = []
    task = asyncio.create_task(bot.notify_app())
    for ev in (
        {"type": "message", "project": "home", "role": "you", "text": "hi"},  # their own words: no push
        {"type": "message", "project": "home", "role": "ken", "text": "  Here's your reel.  "},
        {"type": "typing", "project": "home"},
        {"type": "approval.created", "project": "res", "approval": {"project": "res", "title": "Send the email?"}},
        {"type": "run.finished", "project": "res", "status": "stopped", "title": "x"},
        {"type": "run.finished", "project": "res", "status": "done", "title": "Find flights"},
        {"type": "message", "project": "home", "role": "ken", "text": "Brief " + "x" * 30000,
         "job": {"name": "morning-brief", "label": "☀️ Morning brief", "time": "07:30", "at": 1.0}},
    ):
        engine.q.put_nowait(ev)
    for _ in range(50):
        if len(FakeClient.sent) == 4:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert [s[1] for s in FakeClient.sent] == [
        {"kind": "message", "text": "Here's your reel.", "project": "home", "name": "Ken"},
        {"kind": "approval", "title": "Send the email?", "project": "res", "name": "Researcher"},
        {"kind": "run", "title": "Find flights", "status": "done", "project": "res", "name": "Researcher"},
        {"kind": "message", "text": ("Brief " + "x" * 30000)[:300], "project": "home", "name": "Ken",
         "job": {"name": "morning-brief", "label": "☀️ Morning brief"}, "full_text": ("Brief " + "x" * 30000)[:20000]},
    ]
    assert "job" not in FakeClient.sent[0][1] and "full_text" not in FakeClient.sent[0][1]  # a plain reply
    assert all(s[0] == "https://example.test/app/notify" and s[2] == {"Authorization": "Bearer t0k"} for s in FakeClient.sent)


async def test_a_chat_on_telegram_never_pushes_to_the_app(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(bot, "ENGINE", engine)
    monkeypatch.setattr(bot.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(bot, "ALLOWED_USER_ID", 1)
    monkeypatch.setattr(bot, "LAST_PLACE", "")
    monkeypatch.setenv("KEN_NOTIFY_URL", "https://example.test/app/notify")
    monkeypatch.setenv("KEN_NOTIFY_TOKEN", "t0k")
    FakeClient.sent = []
    task = asyncio.create_task(bot.notify_app())
    for ev in (
        {"type": "message", "project": "home", "role": "you", "text": "?", "via": "telegram"},
        {"type": "message", "project": "home", "role": "ken", "text": "Answered on Telegram", "via": "telegram"},
        {"type": "approval.created", "project": "home", "approval": {"project": "home", "title": "Post it?"}},
        {"type": "message", "project": "home", "role": "ken", "text": "Brief", "job": {"name": "b", "label": "Brief"}},
        {"type": "message", "project": "home", "role": "you", "text": "hi", "via": "app"},
        {"type": "message", "project": "home", "role": "ken", "text": "Back in the app", "via": "app"},
    ):
        engine.q.put_nowait(ev)
    for _ in range(50):
        if len(FakeClient.sent) == 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert [s[1].get("text") for s in FakeClient.sent] == ["Brief", "Back in the app"]
