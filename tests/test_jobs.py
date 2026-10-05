"""Scheduled jobs: on time, caught up after sleep, once a day."""
import os
import tempfile
import time

os.environ.setdefault("KEN_HOME", tempfile.mkdtemp())
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
