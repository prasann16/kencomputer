"""Scheduled jobs: on time, caught up after sleep, once a day."""
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


def test_settings_switch_and_move_a_job():
    import json
    import pytest
    bot.JOBS_FILE.write_text(json.dumps([{**BRIEF, "prompt": "brief"}]))
    assert bot.list_jobs() == [{"name": "morning-brief", "time": "07:00", "days": "daily", "on": True, "agent": "", "agent_name": "",
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
