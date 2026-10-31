"""Connect Claude: the token is saved whether the browser finishes the sign-in or the user pastes a code."""
import asyncio
import os
import sys

import pytest

import onboarding
from onboarding import Onboarding

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the sign-in helper is for the Mac app")

URL = "https://claude.com/cai/oauth/authorize?code=true&client_id=x"
TOKEN = "sk-ant-oat01-abc_DEF-123"


def fake_cli(tmp_path, monkeypatch, script: str):
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\n" + script)
    cli.chmod(0o755)
    monkeypatch.setattr(onboarding, "claude_cli", lambda: str(cli))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


def make(tmp_path):
    calls = []

    async def on_connected():
        calls.append(1)

    setup = Onboarding(tmp_path, lambda: bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), on_connected, lambda: True)
    return setup, calls


async def test_browser_finishes_sign_in_without_a_code(tmp_path, monkeypatch):
    # Approving in the browser hands the sign-in back to the CLI, which prints the token and exits.
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nsleep 0.5\necho "Your token: {TOKEN}"\n')
    setup, calls = make(tmp_path)
    assert (await setup.start()) == {"url": URL}
    for _ in range(50):
        if setup.status()["claude"]:
            break
        await asyncio.sleep(0.1)
    assert setup.status()["claude"]
    assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}" in (tmp_path / ".env").read_text()
    assert calls == [1]
    # A paste arriving afterwards changes nothing.
    assert (await setup.submit("late-code"))["claude"]
    assert calls == [1]
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")


async def test_pasted_code_still_works(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nread code\necho "Your token: {TOKEN}"\n')
    setup, calls = make(tmp_path)
    await setup.start()
    assert (await setup.submit("the-code"))["claude"]
    assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}" in (tmp_path / ".env").read_text()
    assert calls == [1]
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")


async def wait_for_error(setup):
    for _ in range(50):
        if setup.status().get("error"):
            return setup.status()["error"]
        await asyncio.sleep(0.1)
    return ""


async def test_when_claude_says_no_the_screen_says_why(tmp_path, monkeypatch):
    # The CLI draws spaces as cursor moves; the message still reads as words.
    fake_cli(tmp_path, monkeypatch, f'echo "Claude subscription required."\necho "{URL}"\nsleep 0.3\n'
             'printf "OAuth\\033[1Cerror:\\033[1CThis account needs a Claude subscription. Press Enter to retry.\\n"\nsleep 5\n')
    setup, calls = make(tmp_path)
    await setup.start()
    error = await wait_for_error(setup)
    assert error == "Ken needs a Claude Pro or Max plan. Claude said: OAuth error: This account needs a Claude subscription."
    assert calls == [] and not setup.status()["claude"]
    assert (await setup.start())["url"] == URL and "error" not in setup.status()  # trying again clears it
    setup.stop()


async def test_a_sign_in_that_stops_says_so(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nsleep 0.3\n')
    setup, _ = make(tmp_path)
    await setup.start()
    assert "stopped before it finished" in await wait_for_error(setup)


async def test_the_link_is_the_one_the_browser_opened(tmp_path, monkeypatch):
    # In the Mac app the browser's link finishes the sign-in by itself; the printed one needs a code.
    opened = "https://claude.com/cai/oauth/authorize?code=true&redirect_uri=http%3A%2F%2Flocalhost%3A5%2Fcallback"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "open").write_text("#!/bin/sh\n")  # stands in for the real browser
    (bin_dir / "open").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    fake_cli(tmp_path, monkeypatch, f'"$BROWSER" "{opened}"\necho "{URL}"\nsleep 5\n')
    setup, _ = make(tmp_path)
    assert (await setup.start()) == {"url": URL, "browser_url": opened}
    setup.stop()
