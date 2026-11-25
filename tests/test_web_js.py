"""Ken's screens are plain JavaScript (no build step), so a typo anywhere in them is a blank page for everyone.
Every file must at least parse before it can ship (node --check, as the browser would read it)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("name", sorted(p.name for p in WEB.glob("*.js")))
def test_every_screen_script_parses(name, tmp_path):
    # app.js is loaded as a module; .mjs makes node parse it the same way
    copy = tmp_path / (name[:-3] + (".mjs" if name == "app.js" else ".js"))
    copy.write_text((WEB / name).read_text())
    r = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
    assert r.returncode == 0, f"web/{name} doesn't parse:\n{r.stderr[-800:]}"


def test_an_open_tab_cant_wait_forever_for_a_reply():
    # Ken in the cloud: a tab skips the 5-minute hang-up while Ken is answering in it. A "done" that never arrives
    # (the line dropped, Ken restarted mid-reply) must not keep the computer awake for as long as the tab stays open.
    js = (WEB / "app.js").read_text()
    assert "busy.clear()" in js.split("ws.onclose = () =>", 1)[1].split("\n", 1)[0]  # the line drops: forget it
    assert "REPLY_MS = 10 * 60000" in js and "!answering() && Date.now() - lastTouch > IDLE_MS" in js  # and after 10 min


def test_sign_out_and_delete_come_even_if_settings_opened_before_ken_answered():
    # Settings can open before /api/meta says Ken is in the cloud (a reload on #/settings, Ken waking). Asking who's
    # signed in only at opening left Sign out and Delete account missing: it must ask again once meta.hosted arrives.
    js = (WEB / "app.js").read_text()
    line = next(l for l in js.splitlines() if "/account/me.json" in l)
    assert "if (meta.hosted)" in line and line.rstrip().endswith("}, [meta.hosted]);")


def test_new_agent_ideas_fit_each_welcome_profile():
    # Hosted: the New agent screen shows only the ideas that suit what they said they do in the welcome. Each profile
    # needs five or it falls back to everything, and a typo in a tag would quietly hide an idea from that profile.
    js = (WEB / "app.js").read_text()
    block = js.split("const AGENT_IDEAS = [\n", 1)[1].split("\n];", 1)[0]
    tags = [re.search(r'", "([a-z ]+)"\],$', line).group(1).split() for line in block.splitlines()]
    assert len(tags) >= 25
    for profile in ("founder", "creator", "freelancer", "job", "student"):
        assert sum(profile in t for t in tags) >= 5, profile
    assert {p for t in tags for p in t} == {"founder", "creator", "freelancer", "job", "student"}
