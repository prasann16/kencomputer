"""Windows: keeps Ken running, with no console window.

Started at login from the Startup folder (install.ps1 puts a shortcut there). Runs
bot.py, writes its output to ~/.ken/logs/ken.log, and starts it again whenever it
exits, which is how Ken updates itself. Windows can't replace packages a running
Ken has open, so new requirements are installed here, between runs.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
HOME = Path(os.environ.get("KEN_HOME") or APP.parent)
PYTHON = Path(sys.executable).with_name("python.exe")  # we run under pythonw.exe
REQUIREMENTS = APP / "requirements.txt"
INSTALLED = HOME / "requirements.installed"
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def main() -> None:
    (HOME / "logs").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "KEN_HOME": str(HOME), "PYTHONUTF8": "1"}
    while not (HOME / "off").exists():  # turned off with /kill; `ken start` removes the note
        with open(HOME / "logs" / "ken.log", "a", encoding="utf-8") as log:
            wanted = REQUIREMENTS.read_text()
            if not INSTALLED.exists() or INSTALLED.read_text() != wanted:
                pip = [str(PYTHON), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)]
                if subprocess.run(pip, stdout=log, stderr=log, env=env, creationflags=HIDDEN).returncode == 0:
                    INSTALLED.write_text(wanted)
            subprocess.run([str(PYTHON), str(APP / "bot.py")], stdout=log, stderr=log, env=env, cwd=HOME, creationflags=HIDDEN)
        time.sleep(5)


if __name__ == "__main__":
    main()
