"""Ken never fails silently: a background job or a scheduled job always ends with something they can read,
even when the AI is down at the very moment Ken would tell them."""
import asyncio

import pytest

import crash
import engine as engine_mod
from engine import HOME, Engine
from test_engine import FakeBrain, FakeSession

REPORT = "(Your background job"


@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path, FakeBrain())
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    e.agents_dir.mkdir()
    e.requests_dir.mkdir()
    return e


def brain_that(monkeypatch, behave):
    """The fake AI does `behave(prompt, on_text)` instead of answering "did it" (None: answer as usual)."""
    usual = FakeSession.ask

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        self.brain.asks.append({"prompt": prompt, "system": self.system(), "cwd": self.cwd})
        said = await behave(prompt, on_text)
        if said is None:
            self.brain.asks.pop()
            return await usual(self, prompt, on_text, on_tool, on_delta)
        return said

    monkeypatch.setattr(FakeSession, "ask", ask)


async def all_done(engine):
    """Every run, every follow-up run and every report has finished."""
    for _ in range(300):
        await asyncio.sleep(0.02)
        tasks = list(engine.run_tasks.values()) + list(engine._bg)
        if all(t.done() for t in tasks):
            await asyncio.sleep(0.02)
            if all(t.done() for t in list(engine.run_tasks.values()) + list(engine._bg)):
                return
    raise AssertionError("still running")


def ken_lines(engine):
    return [m["text"] for m in engine.messages(HOME) if m["role"] == "ken"]


# ---- A: a background job's report never gets lost

async def test_a_failed_job_whose_report_also_fails_still_says_so(engine, monkeypatch):
    async def behave(prompt, on_text):
        raise Exception("disk full")  # the job fails, and so does Ken's turn to tell them
    brain_that(monkeypatch, behave)
    engine.start_run(HOME, "Instagram posts", "make 30 posts", source="ken")
    await all_done(engine)
    assert ken_lines(engine) == [
        '■ Your background job "Instagram posts" didn\'t finish. Something went wrong on Ken\'s side. Ask me to try again.']
    assert engine.messages(HOME)[-1]["via"] == "background"  # so it reaches the phone too


async def test_a_finished_job_whose_report_fails_says_it_finished_and_what_it_made(engine, monkeypatch):
    async def behave(prompt, on_text):
        if prompt.startswith("make"):
            engine.home_cwd.mkdir(parents=True, exist_ok=True)
            (engine.home_cwd / "posts.md").write_text("30 posts")
            return "Made 30 posts in posts.md"
        raise Exception("disk full")
    brain_that(monkeypatch, behave)
    engine.start_run(HOME, "Instagram posts", "make 30 posts", source="ken")
    await all_done(engine)
    assert engine.list_runs(project=HOME)[0]["status"] == "done"
    (line,) = ken_lines(engine)
    assert line.startswith('✓ Your background job "Instagram posts" is done.') and "`posts.md`" in line


async def test_a_report_that_says_nothing_gets_the_plain_line(engine, monkeypatch):
    async def behave(prompt, on_text):
        return "NOTHING_TO_SAY" if REPORT in prompt else "done"
    brain_that(monkeypatch, behave)
    engine.start_run(HOME, "Tidy up", "tidy", source="ken")
    await all_done(engine)
    assert ken_lines(engine) == ['✓ Your background job "Tidy up" is done.\nAsk me if you want the details.']


async def test_a_report_that_works_is_the_only_message(engine):
    engine.start_run(HOME, "Tidy up", "tidy", source="ken")
    await all_done(engine)
    assert ken_lines(engine) == ["did it"]


# ---- B: a big job carries on past the one-hour limit, a couple of times at most

async def test_a_job_cut_off_by_the_time_limit_carries_on_from_its_files(engine, monkeypatch):
    monkeypatch.setattr(engine_mod, "RUN_LIMIT", 0.1)

    async def behave(prompt, on_text):
        if prompt == "make 30 posts":
            await on_text("Made 12 of 30 posts so far")
            await asyncio.sleep(10)  # cut off by the time limit
        return None
    brain_that(monkeypatch, behave)
    first = engine.start_run(HOME, "Instagram posts", "make 30 posts", source="ken")
    await all_done(engine)
    runs = {r["title"]: r for r in engine.list_runs(project=HOME)}
    assert set(runs) == {"Instagram posts", "Instagram posts (continued)"}
    assert engine._run_row(first["id"])["status"] == "failed"
    nxt = runs["Instagram posts (continued)"]
    assert nxt["status"] == "done" and nxt["source"] == "ken"
    assert nxt["prompt"].startswith("make 30 posts") and "Made 12 of 30 posts so far" in nxt["prompt"]
    assert "Don't start over" in nxt["prompt"] and str(engine.home_cwd) in nxt["prompt"]
    assert ken_lines(engine) == ["did it"]  # carrying on is quiet: they hear only the finished job


async def test_a_job_carries_on_twice_at_most_then_says_it_didnt_finish(engine, monkeypatch):
    monkeypatch.setattr(engine_mod, "RUN_LIMIT", 0.1)

    async def behave(prompt, on_text):
        if REPORT in prompt:
            raise Exception("disk full")  # and the report fails too: the plain line still comes
        await on_text("still going")
        await asyncio.sleep(10)
    brain_that(monkeypatch, behave)
    engine.start_run(HOME, "Instagram posts", "make 30 posts", source="ken")
    await all_done(engine)
    titles = sorted(r["title"] for r in engine.list_runs(project=HOME))
    assert titles == ["Instagram posts", "Instagram posts (continued 2)", "Instagram posts (continued)"]
    briefs = [r["prompt"] for r in engine.list_runs(project=HOME)]
    assert all(p.count("This job already ran for an hour") <= 1 for p in briefs)  # the note isn't stacked up
    assert ken_lines(engine)[-1] == ('■ Your background job "Instagram posts" didn\'t finish. '
                                     "It worked on it for 3 hours and still wasn't done. Ask me to try again.")


async def test_a_run_that_carries_on_isnt_shown_as_failed(engine, monkeypatch):
    monkeypatch.setattr(engine_mod, "RUN_LIMIT", 0.1)

    async def behave(prompt, on_text):
        if prompt == "make 30 posts":
            await asyncio.sleep(10)
        return None
    brain_that(monkeypatch, behave)
    q = engine.subscribe()
    engine.start_run(HOME, "Instagram posts", "make 30 posts", source="ken")
    await all_done(engine)
    finished = [e for e in (q.get_nowait() for _ in range(q.qsize())) if e["type"] == "run.finished"]
    assert [(e["title"], e.get("continued")) for e in finished] == [
        ("Instagram posts", True), ("Instagram posts (continued)", None)]


async def test_a_job_that_went_quiet_doesnt_carry_on(engine, monkeypatch):
    async def behave(prompt, on_text):
        if prompt == "make 30 posts":
            raise asyncio.TimeoutError()  # silent for 45 minutes, well inside the hour: stuck, not big
        return None
    brain_that(monkeypatch, behave)
    engine.start_run(HOME, "Instagram posts", "make 30 posts", source="ken")
    await all_done(engine)
    assert [r["title"] for r in engine.list_runs(project=HOME)] == ["Instagram posts"]
    assert ken_lines(engine) == ["did it"]  # reported as usual


async def test_a_job_they_stopped_or_that_failed_doesnt_carry_on(engine, monkeypatch):
    monkeypatch.setattr(engine_mod, "RUN_LIMIT", 0.5)

    async def behave(prompt, on_text):
        if prompt == "broken":
            raise Exception("disk full")
        if prompt == "long":
            await asyncio.sleep(10)
        return None
    brain_that(monkeypatch, behave)
    engine.start_run(HOME, "Broken", "broken", source="ken")
    long = engine.start_run(HOME, "Long", "long", source="ken")
    await asyncio.sleep(0.1)
    await engine.stop_run(long["id"], by="you")
    await all_done(engine)
    assert sorted(r["title"] for r in engine.list_runs(project=HOME)) == ["Broken", "Long"]


# ---- C: a scheduled job that errors says so; one with nothing to say stays quiet but leaves a trace

async def test_a_scheduled_job_that_fails_says_it_didnt_run(engine, monkeypatch):
    async def behave(prompt, on_text):
        raise Exception("disk full")
    brain_that(monkeypatch, behave)
    heard = []

    async def listener(t):
        heard.append(t)

    tag = {"name": "brand-deals", "label": "⏰ Brand deals"}
    await engine.run_job(HOME, "(find brand deals)", job=tag, listener=listener)
    line = ("⏰ Your brand deals didn't run just now. Something went wrong on Ken's side. "
            "It'll try again next time, or ask me to run it now.")
    assert heard == [line] and ken_lines(engine) == [line]
    assert engine.messages(HOME)[-1]["job"] == tag
    await engine.send_message(HOME, "run it now")
    assert "Brand deals: ⏰ Your brand deals didn't run" in engine.brain.asks[-1]["prompt"]  # the chat knows


async def test_a_one_time_job_that_fails_only_offers_to_run_now(engine, monkeypatch):
    import bot
    tag = bot.job_tag({"name": "call-mum", "time": "09:00", "date": "2026-11-20"})
    assert tag["once"] and "once" not in bot.job_tag({"name": "call-mum", "time": "09:00"})

    async def behave(prompt, on_text):
        raise Exception("disk full")
    brain_that(monkeypatch, behave)
    await engine.run_job(HOME, "(remind)", job=tag)
    assert ken_lines(engine) == ["⏰ Your call mum didn't run just now. Something went wrong on Ken's side. "
                                 "Ask me to run it now."]


async def test_a_job_that_comes_back_empty_says_so_without_offering_a_rerun_as_if_it_never_ran(engine, monkeypatch):
    async def behave(prompt, on_text):
        return ""
    brain_that(monkeypatch, behave)
    await engine.run_job(HOME, "(deals)", job={"name": "brand-deals", "label": "⏰ Brand deals"})
    assert ken_lines(engine) == ["⏰ Your brand deals ran but came back with nothing this time. Ask me if you want it again."]


async def test_an_agents_scheduled_job_that_fails_says_which_job(engine, monkeypatch):
    engine.create_agent("Shop")

    async def behave(prompt, on_text):
        raise Exception("disk full")
    brain_that(monkeypatch, behave)
    tag = {"name": "restock", "label": "⏰ Restock"}
    assert not await engine.send_message("shop", "", log_user=False, prompt="(Scheduled job)", job=tag)
    (msg,) = engine.messages("shop")
    assert msg["error"] and msg["job"] == tag
    assert msg["text"] == ("⏰ Your restock didn't run just now. Something went wrong on Ken's side. "
                           "It'll try again next time, or ask me to run it now.")


async def test_an_agent_job_that_cant_start_says_so_in_kens_chat(engine, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "ENGINE", engine)
    await bot.run_agent_job("deleted-agent", {"name": "restock", "time": "09:00", "prompt": "x"})
    assert ken_lines(engine) == ["⏰ Your restock didn't run just now. Something went wrong on Ken's side. "
                                 "It'll try again next time, or ask me to run it now."]
    assert engine.messages(HOME)[-1]["via"] == "background"


async def test_nothing_to_say_stays_silent_but_is_traced(engine, monkeypatch):
    async def behave(prompt, on_text):
        return "NOTHING_TO_SAY"
    brain_that(monkeypatch, behave)
    tracked = []
    monkeypatch.setattr(crash, "track", lambda name, **props: tracked.append((name, props)))
    heard = []

    async def listener(t):
        heard.append(t)

    assert await engine.run_job(HOME, "(brand deals)", job={"name": "brand-deals", "label": "⏰ Brand deals"},
                                listener=listener) == ""
    assert heard == [] and ken_lines(engine) == []
    assert ("job_quiet", {"builtin": False}) in tracked


async def test_a_job_that_breaks_before_it_starts_isnt_left_running(engine, monkeypatch):
    def broken():
        raise OSError("disk full")
    monkeypatch.setattr(engine, "sync_skills", broken)  # fails before the job reaches the AI at all
    q = engine.subscribe()
    run = engine.start_run(HOME, "Instagram posts", "make 30 posts", source="ken")
    await all_done(engine)
    row = engine._run_row(run["id"])
    assert row["status"] == "failed" and row["finished"]
    assert any(e["type"] == "run.finished" and e["status"] == "failed" for e in (q.get_nowait() for _ in range(q.qsize())))
    assert ken_lines(engine) == [
        '■ Your background job "Instagram posts" didn\'t finish. Something went wrong on Ken\'s side. Ask me to try again.']
    assert engine.messages(HOME)[-1]["via"] == "background"
