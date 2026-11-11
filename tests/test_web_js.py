"""Ken's screens are plain JavaScript (no build step), so a typo anywhere in them is a blank page for everyone.
Every file must at least parse before it can ship (node --check, as the browser would read it)."""
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
