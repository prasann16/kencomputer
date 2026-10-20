import asyncio
import logging
from pathlib import Path

import pytest

import crash


@pytest.fixture
def sent(monkeypatch, tmp_path):
    out = []
    monkeypatch.setenv("KEN_POSTHOG_KEY", "phc_test")
    monkeypatch.delenv("KEN_CRASH_REPORTS", raising=False)
    monkeypatch.setattr(crash, "_state", {"id": "", "version": "", "sent": [], "seen": {}})
    monkeypatch.setattr(crash, "_post", out.append)

    class Now:  # run the "background" send inline
        def __init__(self, target, args, **_):
            self.go = lambda: target(*args)

        def start(self):
            self.go()

    monkeypatch.setattr(crash.threading, "Thread", Now)
    crash._state["id"] = crash.install_id(tmp_path)
    return out


def boom():
    raise ValueError("disk full at " + str(Path.home()) + "/secret.txt")


def test_an_error_becomes_a_posthog_exception(sent):
    try:
        boom()
    except ValueError as e:
        crash.report(e, where="test")
    [p] = sent
    exc = p["properties"]["$exception_list"][0]
    assert p["event"] == "$exception" and exc["type"] == "ValueError"
    assert str(Path.home()) not in exc["value"] and "~/secret.txt" in exc["value"]
    assert exc["stacktrace"]["frames"][-1]["function"] == "boom"
    assert p["distinct_id"] and p["properties"]["$process_person_profile"] is False


def test_tokens_never_leave(sent):
    crash.report(None, "Telegram failed with 1234567890:AAHabcdefghijklmnopqrstuvwxyz012345 and sk-ant-abcdefghijklmnopqrstu")
    msg = sent[0]["properties"]["log_message"]
    assert "AAHabc" not in msg and "sk-ant" not in msg and msg.count("[hidden]") == 2


def test_the_same_error_is_sent_once_an_hour(sent):
    for _ in range(5):
        try:
            boom()
        except ValueError as e:
            crash.report(e)
    assert len(sent) == 1


def test_off_switch_and_no_key(sent, monkeypatch):
    monkeypatch.setenv("KEN_CRASH_REPORTS", "0")
    crash.report(None, "x")
    monkeypatch.delenv("KEN_CRASH_REPORTS")
    monkeypatch.delenv("KEN_POSTHOG_KEY")
    monkeypatch.setattr(crash, "PROJECT_KEY", "")
    crash.report(None, "y")
    assert sent == []


def test_log_exception_and_asyncio_errors_are_caught(sent, tmp_path):
    crash.setup(tmp_path, "test")
    try:
        try:
            boom()
        except ValueError:
            logging.getLogger("ken").exception("Voice transcription failed")
        logging.getLogger("ken").warning("just a warning")

        async def lost():
            raise KeyError("nobody awaited me")

        async def main():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(crash.loop_handler)
            loop.call_exception_handler({"message": "Task exception was never retrieved", "exception": KeyError("x")})

        asyncio.run(main())
    finally:
        for h in list(logging.getLogger().handlers):
            if isinstance(h, crash._Handler):
                logging.getLogger().removeHandler(h)
    kinds = [p["properties"]["$exception_list"][0]["type"] for p in sent]
    assert "ValueError" in kinds and "KeyError" in kinds
    assert all(p["properties"]["ken_version"] == "test" for p in sent)
