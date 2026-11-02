"""What each message costs: counted per model, one message at a time, numbers only."""
import json

import crash
from brains import ClaudeBrain, ClaudeSession, spent_since
from codex_brain import turn_usage


def model_usage(cost, inp=0, out=0, read=0, write=0):
    return {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": read,
            "cacheCreationInputTokens": write, "webSearchRequests": 0, "costUSD": cost}


def test_spent_since_counts_only_what_was_added_per_model():
    before = {"claude-sonnet-5-5": model_usage(0.10, 10, 20, 1000, 500)}
    now = {"claude-sonnet-5-5": model_usage(0.25, 15, 70, 3000, 500), "claude-haiku-5-5": model_usage(0.01, 2, 3, 0, 900)}
    rows = {r["model"]: r for r in spent_since(now, before)}
    assert rows["claude-sonnet-5-5"] == {"model": "claude-sonnet-5-5", "cost_usd": 0.15, "input_tokens": 5,
                                         "output_tokens": 50, "cache_read_tokens": 2000, "cache_write_tokens": 0}
    assert rows["claude-haiku-5-5"]["cost_usd"] == 0.01
    assert spent_since(before, before) == []  # nothing new, no row
    # Claude Code counted from zero again: what it has now is all new.
    assert spent_since({"m": model_usage(0.02)}, {"m": model_usage(0.5)})[0]["cost_usd"] == 0.02


def session_answering(totals, resume=None):
    from claude_agent_sdk import ResultMessage

    class Client:
        async def query(self, prompt):
            pass

        async def receive_messages(self):
            yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                                session_id='s', result='ok', model_usage=totals.pop(0))

    session = ClaudeSession(ClaudeBrain(), '.', '', resume)
    session.client = Client()
    return session


async def noop(text):
    pass


async def test_each_message_counts_only_itself():
    session = session_answering([{"m": model_usage(0.03, out=10)}, {"m": model_usage(0.05, out=25)}])
    await session.ask('one', noop)
    assert [r["cost_usd"] for r in session.usage] == [0.03]
    await session.ask('two', noop)
    assert [(r["cost_usd"], r["output_tokens"]) for r in session.usage] == [(0.02, 15)]


async def test_a_resumed_chat_without_saved_totals_counts_from_its_next_message():
    # Claude Code's totals for a resumed chat include its past: the first answer only sets the baseline.
    session = session_answering([{"m": model_usage(4.0)}, {"m": model_usage(4.1)}], resume='s')
    await session.ask('one', noop)
    assert session.usage == []
    await session.ask('two', noop)
    assert [r["cost_usd"] for r in session.usage] == [0.1]


async def test_a_resumed_chat_with_saved_totals_counts_its_first_message():
    session = session_answering([{"m": model_usage(4.2)}], resume='s')
    session.spent = {"m": model_usage(4.0)}
    await session.ask('one', noop)
    assert [r["cost_usd"] for r in session.usage] == [0.2]


def test_codex_turn_usage_adds_up_the_turn_from_its_updates():
    t = lambda inp, cached, out: {"inputTokens": inp, "cachedInputTokens": cached, "outputTokens": out,
                                  "reasoningOutputTokens": 0, "totalTokens": inp + out, "cacheWriteInputTokens": 0}
    # Thread had 1000 in / 100 out before this turn; the turn made two calls.
    updates = [{"last": t(300, 200, 20), "total": t(1300, 200, 120)},
               {"last": t(400, 350, 30), "total": t(1700, 550, 150)}]
    assert turn_usage(updates, "gpt-6-luna") == [{"model": "gpt-6-luna", "cost_usd": None, "input_tokens": 150,
                                                  "output_tokens": 50, "cache_read_tokens": 550, "cache_write_tokens": 0}]
    assert turn_usage([], None) == []


async def test_engine_records_usage_numbers_only_and_keeps_totals_for_resume(tmp_path, monkeypatch):
    from test_engine import FakeBrain
    from engine import Engine, HOME

    sent = []
    monkeypatch.setattr(crash, "track", lambda name, **props: sent.append((name, props)))
    e = Engine(tmp_path, FakeBrain())
    e.configure_home(tmp_path / "work", lambda: "SOUL", lambda: "WORK")

    class Done:
        session_id = "sess-9"
        spent = {"m": model_usage(0.3)}
        usage = [{"model": "m", "cost_usd": 0.3, "input_tokens": 1, "output_tokens": 2,
                  "cache_read_tokens": 3, "cache_write_tokens": 4}]

    e.record_usage(Done(), HOME, "job", job="custom")
    line = json.loads((tmp_path / "usage.jsonl").read_text())
    assert line["kind"] == "job" and line["job"] == "custom" and line["cost_usd"] == 0.3 and line["brain"] == FakeBrain.name
    assert sent == [("ai_usage", {"kind": "job", "brain": FakeBrain.name, "agent": False, "job": "custom",
                                  "model": "m", "cost_usd": 0.3, "input_tokens": 1, "output_tokens": 2,
                                  "cache_read_tokens": 3, "cache_write_tokens": 4})]
    assert json.loads(e.kv_get("spent:sess-9")) == Done.spent


def test_recording_usage_never_breaks_anything(tmp_path):
    from test_engine import FakeBrain
    from engine import Engine, HOME

    e = Engine(tmp_path, FakeBrain())

    class Broken:
        session_id = "s"
        spent = {"m": object()}  # can't be saved
        usage = [{"model": "m"}]

    e.record_usage(Broken(), HOME, "chat")  # no exception
