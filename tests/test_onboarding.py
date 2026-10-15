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
