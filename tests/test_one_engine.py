"""Only one engine runs per Ken home: a second waits until the first stops, then takes over."""
import os
import subprocess
import sys
import time

FIRST = "import bot, time; lock = bot.one_engine(); print('first', flush=True); time.sleep(30)"
SECOND = "import bot; bot.one_engine(); print('second', flush=True)"


def test_a_second_engine_waits_for_the_first(tmp_path):
    env = {**os.environ, "KEN_HOME": str(tmp_path), "TELEGRAM_BOT_TOKEN": ""}
    first = subprocess.Popen([sys.executable, "-c", FIRST], env=env, stdout=subprocess.PIPE, text=True)
    assert first.stdout.readline().strip() == "first"
    assert (tmp_path / "engine.lock").read_text() == str(first.pid)
    second = subprocess.Popen([sys.executable, "-c", SECOND], env=env, stdout=subprocess.PIPE, text=True)
    time.sleep(2)
    assert second.poll() is None  # still waiting
    first.kill()
    first.wait()
    assert second.stdout.readline().strip() == "second"
    second.wait(timeout=10)
    assert (tmp_path / "engine.lock").read_text() == str(second.pid)
