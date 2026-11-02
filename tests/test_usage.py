"""/usage and the heads-up: plan readings, saying it once per window."""
import os
import tempfile
from pathlib import Path

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
import usage  # noqa: E402
from usage import Usage  # noqa: E402

NOW = 1_793_190_000.0
# What Claude Code actually sends (Oct 28, 2026): the windows' utilization sits in unifiedWindows.
RAW = {"status": "allowed", "resetsAt": NOW + 3600, "rateLimitType": "five_hour", "overageStatus": "rejected",
       "isUsingOverage": False, "unifiedWindows": {"five_hour": {"utilization": 0.12, "resetsAt": NOW + 3600},
                                                   "seven_day": {"utilization": 0.49, "resetsAt": NOW + 3 * 86400}}}


def fresh():
    said = []
    return Usage(Path(tempfile.mkdtemp()), say=said.append), said


def reading(five=0.12, week=0.49, status="allowed", kind="five_hour", reset=NOW + 3600):
    return {**RAW, "status": status, "rateLimitType": kind, "resetsAt": reset,
            "unifiedWindows": {"five_hour": {"utilization": five, "resetsAt": reset},
                               "seven_day": {"utilization": week, "resetsAt": NOW + 3 * 86400}}}


def test_reading_keeps_every_window():
    w = usage.readings_from(RAW, NOW)
    assert w["five_hour"] == {"pct": 12.0, "resets_at": NOW + 3600, "at": NOW, "status": "allowed"}
    assert w["seven_day"]["pct"] == 49.0 and "status" not in w["seven_day"]


def test_reading_without_windows_uses_the_sdk_fields():
    w = usage.readings_from({"status": "allowed_warning", "resetsAt": NOW + 60, "rateLimitType": "seven_day", "utilization": 0.83}, NOW)
    assert w["seven_day"]["pct"] == 83.0 and w["seven_day"]["status"] == "allowed_warning"


def test_near_the_limit():
    assert not usage.near_limit({"pct": 79.9}, NOW)
    assert usage.near_limit({"pct": 80}, NOW)
    assert usage.near_limit({"status": "allowed_warning"}, NOW)
    assert usage.near_limit({"pct": 5, "status": "rejected"}, NOW)
    assert not usage.near_limit({"pct": 99, "resets_at": NOW - 1}, NOW)  # that window is over


def test_heads_up_once_per_window_and_again_after_it_resets():
    u, said = fresh()
    u.record(reading(five=0.5), NOW)
    assert said == []
    u.record(reading(five=0.82), NOW)
    assert said == [f"Heads-up: you've used 82% of your Claude plan's 5 hour limit. It resets {usage.when(NOW + 3600, NOW)}."]
    u.record(reading(five=0.95), NOW)
    assert len(said) == 1  # once per window
    u.record(reading(five=0.81, reset=NOW + 5 * 3600), NOW + 4000)  # the next window
    assert len(said) == 2


def test_usage_text():
    u, _ = fresh()
    assert "No usage reading yet" in u.report("claude", NOW)
    assert "only shown for Claude" in u.report("codex", NOW)
    u.record(RAW, NOW)
    text = u.report("claude", NOW)
    assert "5 hour  ▰▱▱▱▱▱▱▱▱▱ 12%" in text and "Weekly  ▰▰▰▰▰▱▱▱▱▱ 49%" in text


def test_when_today_and_later():
    assert usage.when(None) == "unknown"
    assert ":" in usage.when(NOW + 60, NOW)
