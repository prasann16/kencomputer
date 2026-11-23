"""bot.py's command line: an option it doesn't know never starts a second Ken."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_unknown_option_starts_nothing(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "bot.py"), "--no-such-thing", "x"], capture_output=True, text=True,
                       timeout=60, env={"KEN_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 2 and "--no-such-thing" in r.stdout
    assert not (tmp_path / "engine.lock").exists() and not (tmp_path / "app.db").exists()
