"""Where Ken runs is worked out at every start, and its instructions say so in one place ("=== Where you are ==="):
a computer in the cloud is never told about coffee, a Mac, Esc or their Chrome; the Mac keeps its coffee cup; and each
feature is mentioned only where it exists."""
import os
import re
import sys
import tempfile

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
import bot  # noqa: E402

MAC_ONLY = ("coffee", "COFFEE", "keep the Mac awake", "keeps this Mac awake", "at the computer", "Esc", "Ctrl+C",
            "their Chrome", "user's own Chrome", "/decaf", "/kill", "caffeinate")
CHROME = {"browser": {"type": "stdio", "command": "node", "args": []}}


@pytest.fixture
def on(monkeypatch):
    """Ken on a given platform, with given features present."""
    def set_up(kind, browser=False, telegram=False, voice=True, app_version=""):
        for k in ("KEN_PLATFORM", "KEN_HOSTED", "KEN_APP_VERSION"):
            monkeypatch.delenv(k, raising=False)
        if kind == "cloud":
            monkeypatch.setenv("KEN_PLATFORM", "test-cloud")
        if kind == "server":
            monkeypatch.setenv("KEN_HOSTED", "1")
        if app_version:
            monkeypatch.setenv("KEN_APP_VERSION", app_version)
        monkeypatch.setattr(sys, "platform", {"mac": "darwin", "windows": "win32"}.get(kind, "linux"))
        monkeypatch.setattr(bot, "browser_mcp", lambda: CHROME if browser else {})
        monkeypatch.setattr(bot, "voice_available", lambda: voice)
        monkeypatch.setattr(bot, "video_prompt", lambda: "")  # looks for ffmpeg, which a faked platform can't
        monkeypatch.setattr(bot, "ALLOWED_USER_ID", 42 if telegram else 0)
        return bot.build_system()
    return set_up


def where(system):
    return re.search(r"=== Where you are ===\n(.*?)\n=== end where you are ===", system, re.S).group(1)


@pytest.mark.parametrize("browser", [False, True])
@pytest.mark.parametrize("telegram", [False, True])
def test_a_cloud_ken_never_hears_about_a_mac(on, browser, telegram):
    system = on("cloud", browser=browser, telegram=telegram)
    for word in MAC_ONLY:
        assert word not in system, word
    assert not re.search(r"\bMac\b", system)
    assert "your own computer in the cloud" in where(system) and "wakes by itself" in where(system)
    assert system.count("=== Where you are ===") == 1


def test_a_cloud_agent_gets_its_own_browser_not_their_chrome(on):
    on("cloud", browser=True)
    tools = bot.agent_tools_prompt({"browser"})
    assert "headless Chromium" in tools and "Chrome" not in tools.replace("Chromium", "")
    on("mac", browser=True)
    assert "user's own Chrome" in bot.agent_tools_prompt({"browser"})
    assert bot.agent_tools_prompt(set()) == bot.agent_tools_prompt(set()).replace(bot.BROWSER_PROMPT, "")


def test_the_mac_app_keeps_its_coffee_cup_and_controls(on):
    here = where(on("mac", telegram=True, app_version="1.0.0"))
    assert here.startswith("You run on their own Mac, in the Ken app.")
    assert "COFFEE keeps this Mac awake" in here and "the coffee cup" in here
    assert "/coffee and /decaf, and /kill" in here and "they turn you on again at the computer" in here
    assert "Never suggest Esc or Ctrl+C." in here
    assert "You run on their own Mac." in where(on("mac"))  # a terminal install on a Mac: the same, minus "the Ken app"


@pytest.mark.parametrize("kind,first", [("windows", "You run on their own Windows PC."),
                                        ("linux", "You run on their own Linux computer")])
def test_windows_and_linux_say_where_they_are_and_have_no_coffee(on, kind, first):
    here = where(on(kind, telegram=True))
    assert here.startswith(first)
    assert "While this computer is asleep or off you can't run or answer." in here
    assert "coffee" not in here.lower() and "Mac" not in here
    assert "/kill" in here and "Never suggest Esc or Ctrl+C." in here
    assert "look around this computer" in here  # their own computer: Ken may learn their world from it


def test_each_feature_only_where_it_is(on):
    plain = on("linux", browser=False, telegram=False, voice=False)
    assert "BROWSER:" not in plain and "Voice notes" not in plain
    assert "in Telegram /stop" not in plain and "Telegram is linked" not in plain
    full = on("linux", browser=True, telegram=True, voice=True)
    assert "BROWSER: the `browser` tools drive the user's own Chrome" in full
    assert "Voice notes are transcribed automatically" in full
    assert "in Telegram /stop" in full and "Telegram is linked" in full
    cloud = on("cloud", browser=True)
    assert "BROWSER: you have your own browser" in cloud and "look around this computer" not in cloud


def test_a_server_reached_only_through_telegram(on):
    here = where(on("server"))
    assert "they reach you only through Telegram" in here and "WHERE YOU LIVE" not in here
    assert "in Telegram /stop" in here and "coffee" not in here.lower()


def test_where_is_worked_out_at_every_start(monkeypatch):
    monkeypatch.delenv("KEN_PLATFORM", raising=False)
    monkeypatch.delenv("KEN_HOSTED", raising=False)
    for platform, kind in (("darwin", "mac"), ("win32", "windows"), ("linux", "linux")):
        monkeypatch.setattr(sys, "platform", platform)
        assert bot.platform() == kind
    monkeypatch.setenv("KEN_HOSTED", "1")
    assert bot.platform() == "server"
    monkeypatch.setenv("KEN_PLATFORM", "test-cloud")  # the cloud's computers set both
    assert bot.platform() == "cloud"


def test_a_new_cloud_ken_wakes_up_in_its_own_computer(on):
    bot.BORN_FLAG.unlink(missing_ok=True)
    cloud = on("cloud")
    assert "inside your own computer in the cloud" in cloud and "inside their computer" not in cloud
    assert "inside their computer" in on("mac")
