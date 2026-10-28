"""/usage and the alerts: plan readings, thresholds, saying each thing once, holding jobs back."""
import asyncio
import os
import tempfile
import time
from pathlib import Path

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
import usage  # noqa: E402
from usage import Usage  # noqa: E402

NOW = 1_793_190_000.0
# What Claude Code actually sends (Oct 28, 2026): the windows' utilization sits in unifiedWindows.
RAW = {"status": "allowed", "resetsAt": NOW + 3600, "rateLimitType": "five_hour", "overageStatus": "rejected",
       "isUsingOverage": False, "unifiedWindows": {"five_hour": {"utilization": 0.12, "resetsAt": NOW + 3600},
                                                   "seven_day": {"utilization": 0.49, "resetsAt": NOW + 3 * 86400}}}
GB = 1024 ** 3


def fresh(**kw) -> Usage:
    return Usage(Path(tempfile.mkdtemp()), **kw)


def reading(five=0.12, week=0.49, status="allowed", kind="five_hour"):
    return {**RAW, "status": status, "rateLimitType": kind,
            "unifiedWindows": {"five_hour": {"utilization": five, "resetsAt": NOW + 3600},
                               "seven_day": {"utilization": week, "resetsAt": NOW + 3 * 86400}}}


def test_reading_keeps_every_window():
    w = usage.readings_from(RAW, NOW)
    assert w["five_hour"] == {"pct": 12.0, "resets_at": NOW + 3600, "at": NOW, "status": "allowed"}
    assert w["seven_day"]["pct"] == 49.0 and "status" not in w["seven_day"]


def test_reading_without_windows_uses_the_sdk_fields():
    w = usage.readings_from({"status": "allowed_warning", "resetsAt": NOW + 60, "rateLimitType": "seven_day", "utilization": 0.83}, NOW)
    assert w["seven_day"]["pct"] == 83.0 and w["seven_day"]["status"] == "allowed_warning"


def test_thresholds_and_status():
    assert usage.level({"pct": 79.9}, 80, 90, NOW) == ""
    assert usage.level({"pct": 80}, 80, 90, NOW) == "warn"
    assert usage.level({"pct": 90}, 80, 90, NOW) == "pause"
    assert usage.level({"status": "allowed_warning"}, 80, 90, NOW) == "warn"
    assert usage.level({"pct": 5, "status": "rejected"}, 80, 90, NOW) == "pause"
    assert usage.level({"pct": 99, "resets_at": NOW - 1}, 80, 90, NOW) == ""  # that window is over


def test_thresholds_come_from_env(monkeypatch):
    monkeypatch.setenv("KEN_USAGE_WARN", "60")
    monkeypatch.setenv("KEN_USAGE_PAUSE", "nonsense")
    assert usage.thresholds() == (60.0, 90.0)


def test_warns_once_per_window():
    u = fresh()
    u.record(reading(five=0.82))
    assert u.check_ai(NOW) == [f"AI usage at 82% of your 5 hour window, resets {usage.when(NOW + 3600, NOW)}."]
    u.record(reading(five=0.85))
    assert u.check_ai(NOW + 60) == []
    assert u.check_ai(NOW + 120) == []


def test_status_warning_without_a_number():
    u = fresh()
    u.record({"status": "allowed_warning", "resetsAt": NOW + 3600, "rateLimitType": "five_hour"})
    [line] = u.check_ai(NOW)
    assert line.startswith("AI usage near the limit of your 5 hour window")


def test_pause_then_resume_after_the_reset():
    u = fresh()
    u.record(reading(five=0.91))
    assert u.hold(NOW) and u.skip_job({"name": "daily-content-draft"}, NOW) == "skip"
    assert u.skip_job({"name": "morning-brief"}, NOW) == ""  # the brief always goes out
    [line] = u.check_ai(NOW)
    assert "Pausing background jobs" in line
    assert u.check_ai(NOW + 60) == []  # said once, not every minute
    later = NOW + 3601
    assert not u.hold(later) and u.skip_job({"name": "x"}, later) == ""
    assert u.check_ai(later) == ["AI usage window has reset. Background and scheduled jobs are back on."]
    assert u.check_ai(later + 60) == []


def test_a_rejected_status_pauses():
    u = fresh()
    u.record(reading(five=0.3, status="rejected"))
    assert u.ai_hold(NOW) == NOW + 3600


def test_the_next_window_warns_again():
    u = fresh()
    u.record(reading(five=0.82))
    assert len(u.check_ai(NOW)) == 1
    nxt = reading(five=0.83)
    nxt["unifiedWindows"]["five_hour"]["resetsAt"] = nxt["resetsAt"] = NOW + 5 * 3600
    u.record(nxt)
    assert len(u.check_ai(NOW + 3700)) == 1


def test_pc_pressure_needs_three_minutes_and_says_so_once(monkeypatch):
    monkeypatch.setattr(usage, "top_memory", lambda n=3: [("chromium", int(6.4 * GB))])
    u = fresh()
    tight = {"total": 16 * GB, "available": int(0.8 * GB)}
    assert u.check_pc(tight, (40, 48, 30), 4, NOW) == []
    assert not u.hold(NOW)
    assert u.check_pc(tight, (40, 48, 30), 4, NOW + 120) == []
    [line] = u.check_pc(tight, (40, 48, 30), 4, NOW + 180)
    assert line == "PC under pressure: RAM 95%, load 48 on 4 cores. Pausing new background jobs. Biggest: chromium 6.4GB."
    assert u.hold(NOW) == "the computer is busy" and u.run_cap(2) == 1
    assert u.skip_job({"name": "nightly-review"}, NOW) == "wait"
    assert u.check_pc(tight, (40, 48, 30), 4, NOW + 600) == []
    ok = {"total": 16 * GB, "available": 8 * GB}
    assert u.check_pc(ok, (2, 3, 4), 4, NOW + 660) == []
    assert u.check_pc(ok, (2, 3, 4), 4, NOW + 840) == ["PC back to normal. Background jobs can start again."]
    assert not u.hold(NOW) and u.run_cap(2) == 2


def test_a_short_spike_is_ignored():
    u = fresh()
    tight = {"total": 16 * GB, "available": GB // 2}
    ok = {"total": 16 * GB, "available": 8 * GB}
    u.check_pc(tight, None, 4, NOW)
    u.check_pc(ok, None, 4, NOW + 60)
    assert u.check_pc(tight, None, 4, NOW + 200) == []  # the clock started again
    assert not u.pressure


def test_usage_text_with_readings():
    u = fresh(runs=lambda: (1, 2))
    u.record(reading(), NOW)
    ai = u.ai_text("claude", "claude-opus-5-5", NOW + 180)
    assert ai[0] == "AI: Claude · claude-opus-5-5"
    assert ai[1] == f"5 hour: 12% used, resets {usage.when(NOW + 3600, NOW)}"
    assert ai[2].startswith("Weekly: 49% used, resets ")
    assert ai[3].endswith("(3 min ago)")
    pc = u.pc_text(host="arch", os_="Arch Linux", cpu="Intel i5", n=8, avg=(1.2, 1.5, 1.8), cpu_pct=23.0,
                   mem={"total": 16 * GB, "available": 6 * GB, "swap_total": 4 * GB, "swap_used": GB // 2},
                   top=[("chromium", int(6.4 * GB)), ("python", GB)])
    assert pc == ["PC: arch · Arch Linux", "CPU: Intel i5, 8 cores", "Load 1.2 / 1.5 / 1.8 · CPU 23%",
                  "RAM 10.0GB of 16.0GB used, 6.0GB free · swap 0.5GB of 4.0GB",
                  "Top RAM: chromium 6.4GB, python 1.0GB", "Background jobs: 1 running, 2 waiting"]


def test_usage_text_before_any_reading():
    ai = fresh().ai_text("claude", "", NOW)
    assert ai[0] == "AI: Claude · default model" and ai[1].startswith("No plan usage reading yet")


def test_a_full_report_on_this_computer():
    text = asyncio.run(fresh().report("claude", "claude-opus-5-5"))
    assert "PC: " in text and "Background jobs: 0 running, 0 waiting" in text


def test_when_today_and_later():
    t = time.mktime(time.strptime("2026-10-28 22:09", "%Y-%m-%d %H:%M"))
    assert usage.when(t, t - 3600) == "22:09"
    assert usage.when(t + 2 * 86400, t) == "Fri 22:09"


def test_runs_wait_while_held():
    from engine import Engine, HOME
    from brains import Brain

    class Idle(Brain):
        name = "claude"

    async def go():
        e = Engine(Path(tempfile.mkdtemp()), Idle())
        held = ["the computer is busy"]
        e.run_hold = lambda: held[0]
        e.db.execute("INSERT INTO runs (id, project, title, prompt, source, status, created, activity) VALUES (?,?,?,?,?,?,?,?)",
                     ("r1", HOME, "t", "p", "ken", "queued", time.time(), ""))
        waiting = asyncio.create_task(e._wait_turn("r1", every=0.01))
        await asyncio.sleep(0.05)
        assert not waiting.done() and e._run_row("r1")["activity"] == "waiting: the computer is busy"
        held[0] = ""
        await asyncio.wait_for(waiting, 1)

    asyncio.run(go())
