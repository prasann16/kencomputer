"""Scheduled jobs: on time, caught up after sleep, once a day."""
import json
import os
import tempfile
import time

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
import bot  # noqa: E402

BRIEF = {"name": "morning-brief", "time": "07:00", "days": "daily"}


def at(hhmm, day="2026-10-05"):
    return time.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M")


def test_runs_at_its_time():
    assert bot._job_due(BRIEF, at("07:00"), {})


def test_not_before_its_time():
    assert not bot._job_due(BRIEF, at("06:59"), {})


def test_catches_up_after_the_laptop_slept():
    assert bot._job_due(BRIEF, at("09:40"), {})


def test_too_late_to_catch_up():
    assert not bot._job_due(BRIEF, at("13:01"), {})


def test_only_once_a_day():
    state = {"morning-brief": "2026-10-05 07:00"}
    assert not bot._job_due(BRIEF, at("09:40"), state)
    assert bot._job_due(BRIEF, at("07:00", "2026-10-06"), state)


def test_weekdays_skip_the_weekend():
    job = {**BRIEF, "days": "weekdays"}
    assert not bot._job_due(job, at("07:00", "2026-10-04"), {})  # a Sunday
    assert bot._job_due(job, at("07:00", "2026-10-05"), {})  # a Monday


def test_city_from_the_time_zone():
    assert isinstance(bot.home_city(), str)


def test_new_install_after_the_slot_waits_for_tomorrow():
    state = {"morning-brief": "2026-10-05 (first seen)"}  # what the scheduler records on first sight
    assert not bot._job_due(BRIEF, at("09:40"), state)
    assert bot._job_due(BRIEF, at("07:00", "2026-10-06"), state)


def test_a_job_added_before_its_time_runs_that_day():
    # Added at 00:20 for 00:30: the scheduler's first look at it must not use up its first day.
    job = {"name": "night-loop", "time": "00:30", "days": "daily"}
    state = {}
    assert bot.note_new_jobs([job], at("00:20"), state)
    assert not bot.note_new_jobs([job], at("00:30"), state)  # already known, nothing new to note
    assert bot._job_due(job, at("00:30"), state)


def test_a_job_seen_in_time_still_catches_up_after_sleep():
    # Seen at 00:20, then the laptop slept through 00:30 and Ken looks again at 00:40.
    job = {"name": "night-loop", "time": "00:30", "days": "daily"}
    state = {}
    bot.note_new_jobs([job], at("00:20"), state)
    assert bot._job_due(job, at("00:40"), state)


def test_first_seen_after_its_time_still_waits_for_tomorrow():
    state = {}
    bot.note_new_jobs([BRIEF], at("12:00"), state)  # a new install at noon
    assert not bot._job_due(BRIEF, at("12:00"), state)
    assert bot._job_due(BRIEF, at("07:00", "2026-10-06"), state)


def test_a_switched_off_job_never_runs():
    assert not bot._job_due({**BRIEF, "on": False}, at("07:00"), {})


def test_a_one_time_job_runs_on_its_date_only():
    job = {"name": "reminder", "time": "10:00", "date": "2026-10-31"}
    assert bot._job_due(job, at("10:00", "2026-10-31"), {})
    assert not bot._job_due(job, at("10:00", "2026-11-07"), {})  # the same weekday, a week later
    assert not bot._job_due(job, at("10:00", "2026-10-30"), {})


def test_settings_switch_and_move_a_job():
    import json
    import pytest
    bot.JOBS_FILE.write_text(json.dumps([{**BRIEF, "prompt": "brief"}]))
    assert bot.list_jobs() == [{"name": "morning-brief", "time": "07:00", "days": "daily", "date": "", "on": True, "agent": "", "agent_name": "",
                                "summary": "brief"}]
    assert bot.set_job("morning-brief", on=False)[0]["on"] is False
    assert bot.set_job("morning-brief", at="06:30")[0]["time"] == "06:30"
    assert json.loads(bot.JOBS_FILE.read_text())[0]["prompt"] == "brief"  # the rest of the job is kept
    with pytest.raises(ValueError):
        bot.set_job("morning-brief", at="7am")
    with pytest.raises(ValueError):
        bot.set_job("nope", on=True)


def test_agent_jobs_live_with_their_agent():
    import json
    bot.ENGINE.create_agent("Reader Jobs")
    bot.JOBS_FILE.write_text(json.dumps([
        {**BRIEF, "prompt": "brief"},
        {"name": "money", "time": "08:00", "project": "reader-jobs", "prompt": "Stripe report"},
        {"name": "orphan", "time": "08:00", "project": "gone", "prompt": "x"},
    ]))
    bot.JOB_STATE_FILE.write_text(json.dumps({"money": "2026-10-05 08:00"}))
    bot.migrate_jobs()
    bot.migrate_jobs()  # safe to repeat
    home = json.loads(bot.JOBS_FILE.read_text())
    assert [j["name"] for j in home] == ["morning-brief", "orphan"]  # a job for a missing agent is kept
    assert bot.ENGINE.agent_jobs("reader-jobs") == [{"name": "money", "time": "08:00", "prompt": "Stripe report"}]
    money = next(j for j in bot.all_jobs() if j["name"] == "money")
    assert bot.job_key(money) == "reader-jobs:money"
    state = json.loads(bot.JOB_STATE_FILE.read_text())
    assert not bot._job_due(money, at("09:00"), state)  # already ran today, before the move
    listed = next(j for j in bot.list_jobs() if j["name"] == "money")
    assert listed["agent_name"] == "Reader Jobs"
    bot.set_job("money", on=False, agent="reader-jobs")
    assert bot.ENGINE.agent_jobs("reader-jobs")[0]["on"] is False


def test_old_soul_files_lose_the_app_manual_but_keep_whats_personal(tmp_path):
    soul = tmp_path / "SOUL.md"
    soul.write_text(
        "# You are Ken\n\nYou are Ken, my human's personal assistant. Not a coding session — an assistant. Be direct.\n\n"
        "## Your body — how you physically work\n- You run on my human's own computer.\n\n"
        "## About my human\n- Lives in Porto.\n\n## Memory\n"
        "This file is read at the start of every task; it IS your long-term memory. Keep it rich:\n"
        "- Otherwise don't scatter notes as you go. A nightly review job reads the day's transcript.\n\n"
        "- Prefers voice notes\n"
    )
    bot.tidy_soul(soul)
    text = soul.read_text()
    assert "Your body" not in text and "Not a coding session" not in text and "long-term memory" not in text
    assert "Lives in Porto." in text and "## Remembered\n\n- Prefers voice notes" in text
    assert (tmp_path / f"SOUL.before-{time.strftime('%Y%m%d')}.md").exists()
    bot.tidy_soul(soul)  # nothing left to tidy: untouched
    assert soul.read_text() == text


def test_onboarding_follows_the_checklist_until_its_done():
    import json
    bot.BORN_FLAG.unlink(missing_ok=True)
    bot.SETUP_FILE.unlink(missing_ok=True)
    bot.ensure_setup_file()
    assert "FIRST CONVERSATION EVER" in bot.build_system()  # a new install: Ken opens
    bot.BORN_FLAG.touch()
    data = json.loads(bot.SETUP_FILE.read_text())
    data.update({"ken-name": "done", "call-you": "done"})
    bot.SETUP_FILE.write_text(json.dumps(data))
    system = bot.build_system()
    assert "You have met already" in system and "Still open: work" in system  # picks up where it left off
    data.update({k: "declined" for k in bot.ONBOARDING_ITEMS if data.get(k) == "unset"})
    bot.SETUP_FILE.write_text(json.dumps(data))
    assert "=== Onboarding" not in bot.build_system()  # finished: no more onboarding


def test_an_older_install_isnt_onboarded_again():
    import json
    bot.BORN_FLAG.touch()
    bot.SETUP_FILE.write_text(json.dumps({"brief-time": "done", "city": "done", "calendar": "unset", "email": "unset", "phone": "unset"}))
    bot.ensure_setup_file()
    data = json.loads(bot.SETUP_FILE.read_text())
    assert all(data[k] == "done" for k in bot.ONBOARDING_ITEMS) and data["calendar"] == "unset"


def test_settings_privacy_switch_turns_both_kinds_of_sharing_off_and_on(monkeypatch):
    monkeypatch.delenv("KEN_CRASH_REPORTS", raising=False)
    monkeypatch.delenv("KEN_USAGE_STATS", raising=False)
    assert bot.privacy_status() == {"share": True}  # on by default
    assert bot.set_privacy(False) == {"share": False}
    env = (bot.KEN_HOME / ".env").read_text()
    assert "KEN_CRASH_REPORTS=0" in env and "KEN_USAGE_STATS=0" in env  # remembered across restarts
    assert bot.set_privacy(True) == {"share": True} and "KEN_USAGE_STATS" not in (bot.KEN_HOME / ".env").read_text()
    monkeypatch.setenv("KEN_CRASH_REPORTS", "0")  # leave the tests' own off switch as it was
    monkeypatch.setenv("KEN_USAGE_STATS", "0")



def test_a_job_says_what_it_does():
    import json
    long = "Read yesterday's errors from PostHog and fix the top ones on a branch. Then message me one line."
    bot.JOBS_FILE.write_text(json.dumps([{"name": "crash-review", "time": "09:00", "prompt": long}]))
    assert bot.list_jobs()[0]["summary"] == "Read yesterday's errors from PostHog and fix the top ones on a branch."
    assert bot.job_summary("x" * 200).endswith("…") and len(bot.job_summary("x" * 200)) == 108


def test_a_caught_up_job_says_it_is_late():
    line = bot.late_notice(BRIEF, at("09:40"))
    assert "morning brief" in line and "07:00" in line and "didn't run then" in line


def test_an_on_time_job_says_nothing_extra():
    assert bot.late_notice(BRIEF, at("07:00")) == ""
    assert bot.late_notice(BRIEF, at("07:01")) == ""  # a slow minute is not "late"


def test_untouched_nightly_review_gets_the_disk_check():
    """Any earlier shipped ending is upgraded; one they wrote themselves is left alone."""
    start = "Nightly review. File the day. "
    for old in bot.OLD_NIGHTLY_TAILS:
        bot.JOBS_FILE.write_text(json.dumps([{"name": "nightly-review", "time": "21:00", "prompt": start + old}]))
        bot.refresh_default_jobs()
        prompt = json.loads(bot.JOBS_FILE.read_text())[0]["prompt"]
        assert prompt == start + bot.NEW_NIGHTLY_TAIL
        assert "check disk space" in prompt and "Never delete anything yourself" in prompt
        # Browser profiles are never listed; uploads are, labeled, and they decide.
        assert "Never list browser profiles (~/.ken/signin-browser, /tmp/puppeteer_dev_chrome_profile-*" in prompt
        assert "Chromium profile" in prompt and "uploads included, each labeled with what it is and its size" in prompt
    mine = [{"name": "nightly-review", "time": "21:00", "prompt": "My own review."}]
    bot.JOBS_FILE.write_text(json.dumps(mine))
    bot.refresh_default_jobs()
    assert json.loads(bot.JOBS_FILE.read_text()) == mine


def test_shipped_nightly_review_ends_with_the_current_wording():
    shipped = json.loads((bot.SOURCE / "jobs.default.json").read_text())
    assert next(j for j in shipped if j["name"] == "nightly-review")["prompt"].endswith(bot.NEW_NIGHTLY_TAIL)


def test_untouched_nightly_review_moves_to_midnight_and_reads_yesterday():
    """The 21:00 review missed what was said after it; untouched, it moves to 00:00 and reads the whole day before."""
    shipped = next(j for j in json.loads((bot.SOURCE / "jobs.default.json").read_text()) if j["name"] == "nightly-review")
    assert shipped["time"] == "00:00" and bot.NEW_NIGHTLY_HEAD in shipped["prompt"]
    before = {**shipped, "time": "21:00", "prompt": shipped["prompt"].replace(bot.NEW_NIGHTLY_HEAD, bot.OLD_NIGHTLY_HEAD)}
    bot.JOBS_FILE.write_text(json.dumps([before]))
    bot.refresh_default_jobs()
    assert json.loads(bot.JOBS_FILE.read_text()) == [shipped]
    # A time or wording they chose themselves is left alone.
    for mine in ({**before, "time": "22:30"}, {**before, "prompt": "My own review."}):
        bot.JOBS_FILE.write_text(json.dumps([mine]))
        bot.refresh_default_jobs()
        assert json.loads(bot.JOBS_FILE.read_text()) == [mine]


def test_reel_render_caches_clear_at_the_start_of_each_day(tmp_path):
    """Only the saved graphics go (a speed-up, redrawn on the next edit); everything of theirs stays. Once a day."""
    reel = tmp_path / "videos" / "my-reel"
    (reel / ".layers" / "graphics").mkdir(parents=True)
    (reel / ".layers" / "captions").mkdir()
    keep = [reel / "reel.mp4", reel / "cut.mp4", reel / ".layers" / "captions" / "a.png", tmp_path / "notes.md"]
    for f in keep:
        f.write_text("x")

    def fill():
        (reel / ".layers" / "graphics" / "a.mov").write_text("big")

    fill()
    state = {}
    day1, day2 = time.strptime("2026-11-20 15:00", "%Y-%m-%d %H:%M"), time.strptime("2026-11-21 00:00", "%Y-%m-%d %H:%M")
    assert bot.clear_reel_caches_daily(state, day1, tmp_path)  # first time ever: only notes the day
    assert (reel / ".layers" / "graphics" / "a.mov").exists()
    assert not bot.clear_reel_caches_daily(state, day1, tmp_path)
    assert bot.clear_reel_caches_daily(state, day2, tmp_path)  # a new day: cleared
    assert not (reel / ".layers" / "graphics" / "a.mov").exists() and (reel / ".layers" / "graphics").is_dir()
    assert all(f.exists() for f in keep)
    fill()
    assert not bot.clear_reel_caches_daily(state, day2, tmp_path)  # same day: today's edits keep their files
    assert (reel / ".layers" / "graphics" / "a.mov").exists()


# ---- missed while the computer slept: asked first, never a pile

LOOP = {"name": "job-hunt-loop", "time": "02:30", "days": "daily"}


def test_a_job_on_time_is_not_late():
    assert not bot.is_late(BRIEF, at("07:00"))
    assert not bot.is_late(BRIEF, at("07:04"))  # a slow minute, not a sleep


def test_a_job_caught_up_after_sleep_is_late():
    assert bot.is_late(BRIEF, at("09:40"))


def test_a_missed_job_asks_first_by_default():
    assert bot.missed_policy(LOOP) == "ask"
    assert bot.missed_policy({**LOOP, "missed": "nonsense"}) == "ask"


def test_a_job_can_say_it_catches_up_or_skips():
    assert bot.missed_policy({**LOOP, "missed": "run"}) == "run"
    assert bot.missed_policy({**LOOP, "missed": "Skip"}) == "skip"


def test_a_missed_reminder_still_runs():
    assert bot.missed_policy({"name": "call-mum", "time": "09:00", "date": "2026-10-05"}) == "run"


def test_one_question_lists_everything_missed():
    title, body = bot.missed_text([LOOP, BRIEF])
    assert "2 jobs" in title
    assert "02:30  job hunt loop" in body and "07:00  morning brief" in body
    assert "one after another" in body


def test_one_missed_job_is_named_in_the_question():
    title, body = bot.missed_text([BRIEF])
    assert "morning brief" in title and "jobs" not in title


def test_the_question_warns_when_the_model_is_a_costly_one():
    assert "Fable" in bot.missed_text([LOOP, BRIEF], "claude-fable-5-2")[1]
    assert "fastest" not in bot.missed_text([LOOP, BRIEF], "claude-sonnet-5-5")[1]
    assert "fastest" not in bot.missed_text([BRIEF], "claude-fable-5-2")[1]  # one job is not a pile


def test_asking_files_one_question_and_remembers_its_jobs():
    import asyncio
    state = {}
    asyncio.run(bot.ask_missed([LOOP, BRIEF], state))
    (aid, keys), = state[bot.MISSED_KEY].items()
    assert keys == ["job-hunt-loop", "morning-brief"]
    a = bot.ENGINE.get_approval(aid)
    assert a["kind"] == "missed" and a["status"] == "pending"
    asyncio.run(bot.ENGINE.resolve_approval(aid, "skip"))


def test_a_new_question_closes_the_one_they_never_answered():
    import asyncio
    state = {}
    asyncio.run(bot.ask_missed([LOOP], state))
    old, = state[bot.MISSED_KEY]
    asyncio.run(bot.ask_missed([BRIEF], state))
    assert list(state[bot.MISSED_KEY].values()) == [["morning-brief"]]
    assert bot.ENGINE.get_approval(old)["status"] == "skipped"


def test_approving_runs_the_missed_jobs_one_at_a_time(monkeypatch):
    import asyncio
    bot.JOBS_FILE.write_text(json.dumps([LOOP, BRIEF, {**LOOP, "name": "paused", "on": False}]))
    bot.JOB_STATE_FILE.write_text(json.dumps({bot.MISSED_KEY: {"abc": ["job-hunt-loop", "paused", "gone", "morning-brief"]}}))
    ran, busy = [], []

    async def fake(job):
        assert not busy  # never two at once
        busy.append(1)
        await asyncio.sleep(0)
        ran.append(job["name"])
        busy.pop()

    monkeypatch.setattr(bot, "run_outbound", fake)
    asyncio.run(bot.run_missed("abc"))
    assert ran == ["job-hunt-loop", "morning-brief"]  # a paused or deleted job is left out
    assert json.loads(bot.JOB_STATE_FILE.read_text())[bot.MISSED_KEY] == {}
    asyncio.run(bot.run_missed("abc"))  # a second tap runs nothing
    assert len(ran) == 2


def test_skipping_forgets_the_missed_jobs():
    bot.JOB_STATE_FILE.write_text(json.dumps({bot.MISSED_KEY: {"abc": ["morning-brief"]}}))
    bot.forget_missed("abc")
    assert json.loads(bot.JOB_STATE_FILE.read_text())[bot.MISSED_KEY] == {}


def test_approving_missed_jobs_doesnt_also_hand_them_to_the_chat(monkeypatch):
    import asyncio
    sent = []

    async def fake_send(*a, **k):
        sent.append(k)

    monkeypatch.setattr(bot.ENGINE, "send_message", fake_send)

    async def go():
        a = bot.ENGINE.create_approval(bot.HOME, "missed", "Run the 2 jobs you missed?", "list")
        await bot.ENGINE.resolve_approval(a["id"], "approve")
        await asyncio.sleep(0)

    asyncio.run(go())
    assert sent == []


def test_a_pile_of_background_jobs_on_a_costly_model_gets_a_heads_up():
    assert "3 background jobs" in bot.pile_warning(3, "claude-fable-5-2", 0, 10_000)
    assert bot.pile_warning(2, "claude-fable-5-2", 0, 10_000) == ""  # not a pile yet
    assert bot.pile_warning(5, "claude-sonnet-5-5", 0, 10_000) == ""  # a lighter model
    assert bot.pile_warning(5, "claude-fable-5-2", 9_000, 10_000) == ""  # said it within the hour


def one_scheduler_tick(monkeypatch, jobs, now, state=None):
    """Run the scheduler loop once at `now`; returns the names of the jobs it started."""
    import asyncio
    bot.JOBS_FILE.write_text(json.dumps(jobs))
    bot.JOB_STATE_FILE.write_text(json.dumps(state or {j["name"]: "seen 2026-10-04 20:00" for j in jobs}))
    ran = []

    async def fake(job):
        ran.append(job["name"])

    async def stop(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(bot, "run_outbound", fake)
    monkeypatch.setattr(bot.time, "localtime", lambda *a: now)
    monkeypatch.setattr(bot, "asking_ai", lambda: False)
    monkeypatch.setattr(bot.ENGINE, "is_off", lambda: False)

    async def go():
        real_sleep = asyncio.sleep
        monkeypatch.setattr(bot.asyncio, "sleep", stop)
        try:
            await bot.scheduler()
        except asyncio.CancelledError:
            pass
        monkeypatch.setattr(bot.asyncio, "sleep", real_sleep)
        await real_sleep(0)

    asyncio.run(go())
    return ran


def test_waking_up_asks_about_the_pile_and_runs_only_what_is_on_time(monkeypatch):
    jobs = [{**LOOP, "name": "loop-0230", "time": "02:30"}, {**LOOP, "name": "loop-0430", "time": "04:30"},
            {**LOOP, "name": "light", "time": "05:00", "missed": "run"}, {**LOOP, "name": "never", "time": "05:30", "missed": "skip"},
            {**LOOP, "name": "now", "time": "07:17"}]
    before = {a["id"] for a in bot.ENGINE.pending_approvals()}
    ran = one_scheduler_tick(monkeypatch, jobs, at("07:17"))
    assert sorted(ran) == ["light", "now"]
    state = json.loads(bot.JOB_STATE_FILE.read_text())
    assert list(state[bot.MISSED_KEY].values()) == [["loop-0230", "loop-0430"]]
    new = [a for a in bot.ENGINE.pending_approvals() if a["id"] not in before]
    assert len(new) == 1 and "2 jobs" in new[0]["title"]
    assert all(state[n].startswith("2026-10-05") for n in ("loop-0230", "loop-0430", "never"))  # asked or skipped once, not every minute
    assert one_scheduler_tick(monkeypatch, jobs, at("07:18"), state) == []
