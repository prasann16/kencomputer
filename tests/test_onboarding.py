"""Connect Claude: the token is saved whether the browser finishes the sign-in or the user pastes a code."""
import asyncio
import os

from brains import Brains, ClaudeBrain
from onboarding import Onboarding

URL = "https://claude.com/cai/oauth/authorize?code=true&client_id=x"
TOKEN = "sk-ant-oat01-abc_DEF-123"


def fake_cli(tmp_path, monkeypatch, script: str):
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\n" + script)
    cli.chmod(0o755)
    monkeypatch.setattr(ClaudeBrain, "cli", staticmethod(lambda: str(cli)))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


class Claude(ClaudeBrain):
    def connected(self):  # only the token this test saves, not this machine's own sign-in
        return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))


def make(tmp_path):
    calls = []

    async def on_connected():
        calls.append(1)

    setup = Onboarding(tmp_path, Brains([Claude()]), on_connected, lambda: True)
    return setup, calls


def claude(status) -> bool:
    return next(b["connected"] for b in status["brains"] if b["name"] == "claude")


async def test_browser_finishes_sign_in_without_a_code(tmp_path, monkeypatch):
    # Approving in the browser hands the sign-in back to the CLI, which prints the token and exits.
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nsleep 0.5\necho "Your token: {TOKEN}"\n')
    setup, calls = make(tmp_path)
    started = await setup.start("claude")
    assert started["url"] == URL and started["paste"]
    for _ in range(50):
        if claude(setup.status()):
            break
        await asyncio.sleep(0.1)
    assert claude(setup.status())
    assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}" in (tmp_path / ".env").read_text()
    assert calls == [1]
    # A paste arriving afterwards changes nothing.
    assert claude(await setup.submit("late-code"))
    assert calls == [1]
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")


async def test_pasted_code_still_works(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nread code\necho "Your token: {TOKEN}"\n')
    setup, calls = make(tmp_path)
    await setup.start("claude")
    assert claude(await setup.submit("the-code"))
    assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}" in (tmp_path / ".env").read_text()
    assert calls == [1]
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
