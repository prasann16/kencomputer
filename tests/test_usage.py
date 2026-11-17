"""/usage and the heads-up: plan readings (Claude and ChatGPT), saying it once per window, memory."""
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
    assert not usage.near_limit({"pct": 89.9}, NOW)
    assert usage.near_limit({"pct": 90}, NOW)
    assert usage.near_limit({"status": "allowed_warning"}, NOW)  # no percentage: the plan's own warning counts
    assert not usage.near_limit({"pct": 83, "status": "allowed_warning"}, NOW)  # with one, 90% is the line
    assert usage.near_limit({"pct": 5, "status": "rejected"}, NOW)
    assert not usage.near_limit({"pct": 99, "resets_at": NOW - 1}, NOW)  # that window is over


def test_heads_up_once_per_window_and_again_after_it_resets():
    u, said = fresh()
    u.record(reading(five=0.5), NOW)
    assert said == []
    u.record(reading(five=0.85), NOW)
    assert said == []  # 85%: not yet
    u.record(reading(five=0.92), NOW)
    assert said == [f"Heads-up: you've used 92% of your Claude plan's 5 hour limit. It resets {usage.when(NOW + 3600, NOW)}."]
    u.record(reading(five=0.95), NOW)
    assert len(said) == 1  # once per window
    u.record(reading(five=0.91, reset=NOW + 5 * 3600), NOW + 4000)  # the next window
    assert len(said) == 2


GB = 1024 ** 3


def test_usage_text():
    u, _ = fresh()
    assert "No reading yet" in u.report("claude", NOW, mem={})
    u.record(RAW, NOW)
    text = u.report("claude", NOW, mem={"total": 16 * GB, "available": 6 * GB}, ken=1.5 * GB)
    assert "5 hour  ▰▱▱▱▱▱▱▱▱▱ 12%" in text and "Weekly  ▰▰▰▰▰▱▱▱▱▱ 49%" in text
    assert "Memory  ▰▰▰▰▰▰▱▱▱▱ 62% · 10.0GB of 16.0GB" in text and "Ken is using 1.5GB of it" in text


# What Codex's account/rateLimits/read answers (openai-codex 0.163).
CODEX = {"primary": {"usedPercent": 94, "windowDurationMins": 300, "resetsAt": int(NOW + 1800)},
         "secondary": {"usedPercent": 30, "windowDurationMins": 10080, "resetsAt": int(NOW + 4 * 86400)},
         "planType": "plus", "rateLimitReachedType": None}


def test_chatgpt_plan_readings_and_heads_up():
    u, said = fresh()
    u.record_codex(CODEX, NOW)
    assert said == [f"Heads-up: you've used 94% of your ChatGPT plan's 5 hour limit. It resets {usage.when(NOW + 1800, NOW)}."]
    text = u.report("codex", NOW, mem={})
    assert text.startswith("Your ChatGPT plan:") and "5 hour  ▰▰▰▰▰▰▰▰▰▱ 94%" in text and "Weekly  ▰▰▰▱▱▱▱▱▱▱ 30%" in text
    assert "No reading yet" in u.report("claude", NOW, mem={})  # each plan keeps its own readings


def test_this_computers_memory_is_read():
    mem = usage.memory()
    assert mem["total"] > 0 and 0 <= mem["available"] <= mem["total"]
    assert usage.ken_memory() > 0 or not usage._run("ps", "-o", "pid=")


def test_when_today_and_later():
    assert usage.when(None) == "unknown"
    assert ":" in usage.when(NOW + 60, NOW)
    assert usage.when(NOW + 20 * 86400, NOW)[:3].isalpha() and ":" not in usage.when(NOW + 20 * 86400, NOW)  # 'Nov 23'


def test_a_free_chatgpt_plan_has_a_monthly_window():
    u, _ = fresh()
    u.record_codex({"primary": {"usedPercent": 4, "windowDurationMins": 43200, "resetsAt": int(NOW + 20 * 86400)},
                    "secondary": None, "planType": "free"}, NOW)
    assert "Monthly  ▱▱▱▱▱▱▱▱▱▱ 4%" in u.report("codex", NOW, mem={})


def test_low_memory_is_said_after_two_minutes_once_a_day():
    u, _ = fresh()
    assert not u.low_memory(True, NOW)
    assert not u.low_memory(True, NOW + 60)
    assert u.low_memory(True, NOW + 120)
    assert not u.low_memory(True, NOW + 180)  # once a day
    assert not u.low_memory(False, NOW + 240)
    assert not u.low_memory(True, NOW + 300) and not u.low_memory(True, NOW + 400)  # same day, even after it eased


def test_a_short_spike_is_not_news():
    u, _ = fresh()
    assert not u.low_memory(True, NOW)
    assert not u.low_memory(False, NOW + 60)
    assert not u.low_memory(True, NOW + 120)  # it starts over
    assert not u.low_memory(None, NOW + 300)  # can't tell: nothing to say


def test_low_memory_says_who_is_using_it():
    u, _ = fresh()
    other = u.low_memory_text(("Google Chrome", 6 * GB), int(0.5 * GB), [])
    assert "running out of memory. Google Chrome is using 6.0GB" in other
    ken = u.low_memory_text(("Google Chrome", 1 * GB), 4 * GB, ["Make the launch reel"])
    assert 'Ken is using 4.0GB of it for the background job "Make the launch reel". Say stop' in ken
    assert u.low_memory_text(("", 0), 0, []).endswith("running out of memory. Closing some apps or windows will help.")


def test_the_memory_check_is_cheap_and_reads_this_computer():
    import time as t
    start = t.perf_counter()
    for _ in range(100):
        state = usage.memory_critical()
    assert state in (True, False)
    if usage.sys.platform == "darwin":
        assert t.perf_counter() - start < 0.5  # a sysctl, not a program
    app, size = usage.biggest_app()
    assert (app and size > 0) or usage.os.name == "nt"  # Windows has no `ps`: the alert says so plainly


async def test_the_watcher_stops_cleanly(monkeypatch):
    import asyncio
    u, said = fresh()
    monkeypatch.setattr(usage, "memory_critical", lambda: True)
    monkeypatch.setattr(usage, "LOW_MEMORY_FOR", 0)
    monkeypatch.setattr(usage, "biggest_app", lambda: ("Safari", 3 * GB))
    monkeypatch.setattr(usage, "ken_memory", lambda: GB)
    task = asyncio.create_task(u.watch_memory(every=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(said) == 1 and "Safari is using 3.0GB" in said[0]


def test_app_names_people_know():
    assert usage.app_name("/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)") == "Google Chrome"
    assert usage.app_name("/System/Library/Frameworks/Virtualization.framework/Versions/A/XPCServices/com.apple.Virtualization.VirtualMachine.xpc/Contents/MacOS/com.apple.Virtualization.VirtualMachine") == "VirtualMachine"
    assert usage.app_name("/usr/bin/python3") == "python3" and usage.app_name("firefox") == "firefox"
    assert usage.app_name("C:\\Program Files\\chrome.exe".replace("\\", "/")) == "chrome"


# -- the disk filling up (5 GB, a small computer in the cloud)
GB = 1024 ** 3


def test_disk_says_each_level_once():
    u, _ = fresh()
    assert u.disk_check(5 * GB, int(2 * GB)) == ""          # 60% full
    assert u.disk_check(5 * GB, int(0.9 * GB)) == "warn"    # 82%
    assert u.disk_check(5 * GB, int(0.8 * GB)) == ""        # still over 80%: said already
    assert not u.disk_full
    assert u.disk_check(5 * GB, int(0.4 * GB)) == "stop"    # 92%: stop the work
    assert u.disk_full
    assert u.disk_check(5 * GB, int(0.3 * GB)) == ""


def test_disk_hovering_at_the_line_says_nothing_new():
    u, _ = fresh()
    assert u.disk_check(5 * GB, int(0.4 * GB)) == "stop"
    assert u.disk_check(5 * GB, int(0.55 * GB)) == ""       # 89%: just under, still stopped
    assert u.disk_full
    assert u.disk_check(5 * GB, int(0.9 * GB)) == ""        # 82%: back to the heads-up level, already said
    assert not u.disk_full
    assert u.disk_check(5 * GB, int(1.5 * GB)) == ""        # 70%: room again
    assert u.disk_check(5 * GB, int(0.4 * GB)) == "stop"    # full again later: said again


def test_a_big_disk_with_room_left_is_fine():
    u, _ = fresh()
    assert u.disk_check(1000 * GB, 100 * GB) == ""          # 90% full but 100 GB left


def test_disk_texts():
    u, _ = fresh()
    stop = u.disk_text("stop", 5 * GB, int(0.4 * GB), "get more space with a bigger plan: https://kencomputer.dev/account")
    assert stop.startswith("Your ") and "92% full" in stop and "stopped what I was doing" in stop and "/account" in stop
    warn = u.disk_text("warn", 5 * GB, int(0.9 * GB), "free up some space on it")
    assert warn.startswith("Heads-up: your ") and "82% full" in warn


def test_watch_disk_stops_work_before_saying_so(monkeypatch):
    import asyncio
    import shutil
    from collections import namedtuple
    u, said = fresh()
    order = []
    monkeypatch.setattr(shutil, "disk_usage", lambda p: namedtuple("D", "total used free")(5 * GB, 0, int(0.4 * GB)))

    async def stop():
        order.append("stopped")

    async def go():
        task = asyncio.create_task(u.watch_disk(stop, "more", every=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(go())
    assert order == ["stopped"] and len(said) == 1 and "stopped what I was doing" in said[0]


def watch(u, monkeypatch, free_gb, total_gb=20, ask=None):
    """One minute of watch_disk on a fake disk; returns (stopped?, briefs it asked with)."""
    import asyncio
    import shutil
    from collections import namedtuple
    monkeypatch.setattr(shutil, "disk_usage", lambda p: namedtuple("D", "total used free")(total_gb * GB, 0, int(free_gb * GB)))
    stopped, briefs = [], []

    async def stop():
        stopped.append(True)

    async def go():
        task = asyncio.create_task(u.watch_disk(stop, "more", every=10, ask=ask or (lambda b: briefs.append(b) or True)))
        await asyncio.sleep(0.02)
        task.cancel()
    asyncio.run(go())
    return bool(stopped), briefs


def test_disk_with_room_asks_nothing_and_clears_nothing(monkeypatch):
    """Under 75% nothing happens; and over it Ken only asks: no file is ever removed without their yes."""
    u, said = fresh()
    keep = u.path.parent / "work" / "videos" / "v1_nomusic.mp4"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"x" * 1000)
    assert watch(u, monkeypatch, free_gb=6) == (False, [])          # 70% full
    stopped, briefs = watch(u, monkeypatch, free_gb=4.4)            # 78%: the list is made, nothing deleted
    assert not stopped and len(briefs) == 1 and keep.exists() and said == []
    assert "DELETE NOTHING" in briefs[0] and "78% full" in briefs[0] and "Clear them?" in briefs[0]


def test_the_list_never_offers_browser_profiles_or_their_uploads():
    u, _ = fresh()
    brief = u.clear_brief(20 * GB, int(4 * GB))
    home = u.path.parent
    assert f"Never offer a browser profile or signed-in browser: {home / 'signin-browser'}" in brief
    assert "puppeteer_dev_chrome_profile-*" in brief and "Chromium user-data" in brief
    assert f"Never offer their own uploads in {home / 'inbox'}" in brief


def test_the_list_comes_once_a_day_or_when_it_gets_worse():
    u, _ = fresh()
    assert u.clear_due(20 * GB, int(4.4 * GB), now=NOW)                     # 78%
    u.asked_to_clear(20 * GB, int(4.4 * GB), now=NOW)
    assert not u.clear_due(20 * GB, int(3.2 * GB), now=NOW + 3600)          # 84% but the same day: no
    assert not u.clear_due(20 * GB, int(4.4 * GB), now=NOW + 86400)         # next day, no worse (they said no): no
    assert u.clear_due(20 * GB, int(3.2 * GB), now=NOW + 86400)             # next day and worse: asks again
    assert not u.clear_due(1000 * GB, 100 * GB, now=NOW + 86400)            # 90% of a big disk, 100 GB left: fine
    assert u.clear_due(20 * GB, int(1.6 * GB), stop=True, now=NOW + 60)     # the stop level always asks
    assert not u.clear_due(20 * GB, int(8 * GB), now=NOW + 86400)           # 60%: room again (they cleared it)
    assert u.clear_due(20 * GB, int(4.4 * GB), now=NOW + 86400)             # filling up again later: a new ask


def test_stop_level_pauses_and_the_list_comes_with_it(monkeypatch):
    u, said = fresh()
    u.asked_to_clear(20 * GB, int(4.4 * GB))                         # asked earlier today already
    stopped, briefs = watch(u, monkeypatch, free_gb=1.6)             # 92%
    assert stopped and len(briefs) == 1 and "paused all work" in briefs[0]
    assert len(said) == 1 and "stopped what I was doing" in said[0] and "list of what's safe to clear" in said[0]


def test_no_list_when_it_cant_start_says_so_plainly(monkeypatch):
    u, said = fresh()
    stopped, _ = watch(u, monkeypatch, free_gb=1.6, ask=lambda b: False)    # Ken off: no job
    assert stopped and "Tell me what to do" in said[0]
    assert "disk_asked" not in u.load()


def test_settings_meters_and_when_a_full_plan_opens_again():
    """Settings → AI shows each plan's windows from the readings replies brought (no AI call); a plan at its limit
    says when it opens again."""
    u, _ = fresh()
    assert u.limits(NOW) == {"claude": [], "codex": []} and u.resets("claude", NOW) == ""
    u.record(reading(five=1.0, status="rejected"), NOW)
    u.record_codex(CODEX, NOW)
    limits = u.limits(NOW)
    assert [(w["label"], w["pct"], w["near"]) for w in limits["claude"]] == [("5 hour", 100.0, True), ("Weekly", 49.0, False)]
    assert limits["claude"][0]["reset"] == usage.when(NOW + 3600, NOW)
    assert [w["label"] for w in limits["codex"]] == ["5 hour", "Weekly"]
    assert u.resets("claude", NOW) == usage.when(NOW + 3600, NOW)
    assert [w["label"] for w in u.limits(NOW + 7200)["claude"]] == ["Weekly"]  # a window past its reset drops off
    assert u.resets("claude", NOW + 7200) == ""
