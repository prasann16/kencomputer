"""Connect Claude: the token is saved whether the browser finishes the sign-in or the user pastes a code."""
import asyncio
import os
import sys

import pytest

import onboarding
from onboarding import Onboarding

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the sign-in helper is for the Mac app")

URL = "https://claude.com/cai/oauth/authorize?code=true&client_id=x"
TOKEN = "sk-ant-oat01-" + "abc_DEF-123" * 5  # the real shape: sk-ant-oat, two digits, 40+ characters


def fake_cli(tmp_path, monkeypatch, script: str):
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\n" + script)
    cli.chmod(0o755)
    monkeypatch.setattr(onboarding, "claude_cli", lambda: str(cli))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    checked.clear()

    async def check(token="", api_key=""):  # Anthropic says the whole token works, and nothing else
        checked.append(token)
        return "" if token == TOKEN else "Claude didn’t accept that. Check you copied all of it and try again."
    monkeypatch.setattr(onboarding, "check_claude", check)


checked: list[str] = []


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


async def test_a_token_missing_its_o_is_saved_whole(tmp_path, monkeypatch):
    # The CLI's terminal codes can eat the "o" of "oat"; what's saved is the real token.
    eaten = TOKEN.replace("sk-ant-oat", "sk-ant-at")
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nsleep 0.3\necho "Your token: {eaten}"\n')
    setup, calls = make(tmp_path)
    await setup.start()
    for _ in range(50):
        if setup.status()["claude"]:
            break
        await asyncio.sleep(0.1)
    env = (tmp_path / ".env").read_text()
    assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}" in env and f"={eaten}" not in env
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


async def test_a_rejected_code_says_so_at_once(tmp_path, monkeypatch):
    # The CLI rejects a bad code within seconds and waits for Enter: say why, don't wait out the minute.
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nread code\necho "OAuth error: Request failed with status code 400 Press Enter to retry."\nsleep 30\n')
    setup, calls = make(tmp_path)
    await setup.start()
    started = asyncio.get_running_loop().time()
    with pytest.raises(RuntimeError, match=r"Claude didn’t accept that code \(OAuth error: Request failed with status code 400\)"):
        await setup.submit("bad-code")
    assert asyncio.get_running_loop().time() - started < 5
    assert calls == []


async def test_connecting_claude_while_on_ken_ai_credits_sends_the_code(tmp_path, monkeypatch):
    # Ken AI credits count as "connected", but someone on them pasting a code is connecting their own Claude: the code
    # must reach the sign-in, and their own token is saved.
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://host.example/ai/")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "person-token")
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nread code\necho "got $code" >> "{tmp_path}/codes"\necho "Your token: {TOKEN}"\n')
    calls = []

    async def on_connected():
        calls.append(1)

    setup = Onboarding(tmp_path, lambda: True, on_connected, lambda: True)  # Ken AI credits: already "connected"
    await setup.start()
    assert (await setup.submit("the-code"))["claude"]
    assert (tmp_path / "codes").read_text() == "got the-code\n"
    assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}" in (tmp_path / ".env").read_text()
    assert calls == [1]
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")


async def test_a_code_for_a_sign_in_that_went_away_opens_a_new_one_and_says_so(tmp_path, monkeypatch):
    # Connect shows this error (web/app.js shows r.error with the new sign-in link): never a dead end.
    for k in ("KEN_INCLUDED_AI_URL", "KEN_INCLUDED_AI_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nsleep 5\n')
    setup = Onboarding(tmp_path, lambda: False, lambda: None, lambda: True)
    r = await setup.submit("a-code")  # never started
    assert r["url"] == URL
    assert r["error"] == "That sign-in had expired, so a new one just opened. Approve it, then paste the new code."
    setup.stop()


async def test_the_page_hears_why_a_code_went_nowhere(tmp_path, monkeypatch):
    # Through BrainOnboarding, as /api/setup calls it: the computer restarted between Connect and the pasted code.
    for k in ("KEN_INCLUDED_AI_URL", "KEN_INCLUDED_AI_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nsleep 5\n')
    claude = Onboarding(tmp_path, lambda: False, lambda: None, lambda: True)
    r = await onboarding.BrainOnboarding({"claude": claude}, lambda: "claude").submit("a-code")
    assert r["url"] == URL
    assert "expired" in r["error"]
    claude.stop()


def test_the_browser_opener_never_calls_itself(tmp_path):
    """On Linux, open and xdg-open hand the link to $BROWSER, which was the opener itself: a fork bomb that restarted
    every Ken in the cloud a minute or two after Connect Claude. Here a fake xdg-open does just that."""
    import subprocess
    import onboarding
    opener, calls = tmp_path / "signin-browser", tmp_path / "calls"
    opener.write_text(onboarding.OPENER)
    opener.chmod(0o700)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("open", "xdg-open"):  # what Debian has: both end up in $BROWSER
        (bin_ / name).write_text(f'#!/bin/sh\necho {name} >> "{calls}"\n[ -n "$BROWSER" ] && exec "$BROWSER" "$1"\nexit 0\n')
        (bin_ / name).chmod(0o700)
    link = "https://claude.com/cai/oauth/authorize?code=true"
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "BROWSER": str(opener)}
    subprocess.run([str(opener), link], env=env, timeout=10, check=True)
    assert (tmp_path / "signin-browser.url").read_text() == link
    if sys.platform == "darwin":  # a Mac's open, once, and it can't come back
        assert calls.read_text().split() == ["open"]
        return
    assert not calls.exists()  # Linux with no screen (a computer in the cloud): nothing opens
    subprocess.run([str(opener), link], env={**env, "DISPLAY": ":0"}, timeout=10, check=True)
    assert calls.read_text().split() == ["xdg-open"]  # with a screen: xdg-open once, and it can't come back


async def test_reconnecting_over_a_dead_sign_in_waits_for_the_code_and_takes_it(tmp_path, monkeypatch):
    # A saved sign-in that Claude no longer accepts still counts as "connected": Reconnect must not see it and close
    # before the box for the code shows, and the pasted code must replace it.
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nread code\necho "Your token: {TOKEN}"\n')
    old = "sk-ant-oat01-" + "dead" * 12
    onboarding.save_env(tmp_path / ".env", "CLAUDE_CODE_OAUTH_TOKEN", old)
    setup, calls = make(tmp_path)
    await setup.start()
    assert setup.status()["connected"] is False and setup.status()["renewing"]
    assert (await setup.submit("the-code"))["claude"]
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN and old not in (tmp_path / ".env").read_text()
    assert calls == [1] and "renewing" not in setup.status()
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")


async def test_a_sign_in_claude_no_longer_accepts_is_forgotten(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    setup, calls = make(tmp_path)
    onboarding.save_env(tmp_path / ".env", "CLAUDE_CODE_OAUTH_TOKEN", TOKEN)

    async def works(**_):
        return ""

    monkeypatch.setattr(onboarding, "check_claude", works)  # still good, or Anthropic out of reach: kept
    assert not await setup.forget_if_dead() and setup.status()["claude"]

    async def refused(**_):
        return "Claude didn’t accept that."

    monkeypatch.setattr(onboarding, "check_claude", refused)
    assert await setup.forget_if_dead()
    assert not setup.status()["claude"] and "CLAUDE_CODE_OAUTH_TOKEN" not in (tmp_path / ".env").read_text()
    assert not await setup.forget_if_dead()  # nothing left to check


async def test_a_dead_sign_in_never_moves_them_to_ken_ai_credits_by_itself(tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "KEN_USE_CREDITS", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(name, "x")  # so whatever this sets is undone after the test
        monkeypatch.delenv(name)
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://credits.example/v1")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "included-key")
    setup, calls = make(tmp_path)
    onboarding.save_env(tmp_path / ".env", "CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    onboarding.use_included_ai()

    async def refused(**_):
        return "Claude didn’t accept that."

    monkeypatch.setattr(onboarding, "check_claude", refused)
    assert await setup.forget_if_dead()
    assert not onboarding.included_ai_in_use() and setup.status()["credits_off"] and not setup.status()["claude"]
    await setup.use_credits(True)  # they pick Ken AI credits in Settings → AI
    assert onboarding.included_ai_in_use() and "credits_off" not in setup.status()
    await setup.use_credits(False)
    await setup.disconnect()  # disconnecting by hand is their choice: credits as before
    assert onboarding.included_ai_in_use()


async def test_a_token_still_being_printed_is_never_saved_half(tmp_path, monkeypatch):
    # The CLI's output arrives in pieces: half a token on screen is not a token yet.
    half, rest = TOKEN[:60], TOKEN[60:]  # long enough to look like a token on its own
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nsleep 0.3\nprintf "Your token: {half}"\nsleep 1\necho "{rest}"\nsleep 0.3\n')
    setup, calls = make(tmp_path)
    await setup.start()
    for _ in range(50):
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            break
        await asyncio.sleep(0.1)
    assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == TOKEN
    assert half not in checked  # never even tried
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")


async def test_a_token_anthropic_refuses_is_not_saved_and_the_screen_says_so(tmp_path, monkeypatch):
    bad = "sk-ant-oat01-" + "zzz_ZZZ-999" * 5
    fake_cli(tmp_path, monkeypatch, f'echo "{URL}"\nread code\necho "Your token: {bad}"\n')
    setup, calls = make(tmp_path)
    await setup.start()
    with pytest.raises(RuntimeError, match="didn’t accept"):
        await setup.submit("the-code")
    assert not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and "CLAUDE_CODE_OAUTH_TOKEN" not in ((tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else "")
    assert calls == []
