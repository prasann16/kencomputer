"""Their own Claude or ChatGPT plan at its limit: Ken says so and how to keep going (Ken AI credits in Settings → AI),
but never switches to Ken AI credits by itself. A switch an older Ken made still goes back when their plan resets."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import onboarding  # noqa: E402
from brains import AIUnavailable  # noqa: E402
from engine import HOME, Engine  # noqa: E402
from test_engine import FakeBrain, FakeSession  # noqa: E402

LIMIT = ("You've reached your Claude plan's limit for now. It resets at 22:20. "
         "To keep going now, switch to Ken AI credits in Settings → AI.")


class PlanSession(FakeSession):
    """Answers on Ken AI credits, or on their own plan unless it's at its limit (Claude's reading says so as it fails)."""

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        brain, plan = self.brain, self.brain.plan
        credits = brain.name == "claude" and onboarding.included_ai_in_use()
        brain.asks.append({"prompt": prompt, "credits": credits})
        if credits and plan.credits_out:
            raise AIUnavailable(402, "billing_error")
        if not credits and plan.limited.get(brain.name):
            plan.resets[brain.name] = "22:20"
            if plan.act_first and on_tool:
                await on_tool("Sending the email")
            raise AIUnavailable(429, "rate_limit")
        reply = f"answer on {'credits' if credits else brain.name}"
        await on_text(reply)
        return reply


class PlanBrain(FakeBrain):
    def __init__(self, name, plan):
        super().__init__()
        self.name, self.plan = name, plan

    def session(self, *, cwd, system, resume=None, model_fn=None, mcp_fn=None, env_fn=None):
        return PlanSession(self, cwd, system, resume, model_fn, mcp_fn, env_fn)


class Plan:
    def __init__(self):
        self.limited, self.resets, self.credits_out, self.act_first = {}, {}, False, False


@pytest.fixture
def ken(tmp_path, monkeypatch):
    """A hosted Ken with Ken AI credits, where they've connected their own Claude."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "KEN_USE_CREDITS", "KEN_CREDITS_FOR_NOW"):
        monkeypatch.setenv(name, "x")  # so whatever Ken sets is undone after the test
        monkeypatch.delenv(name)
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://credits.example/v1")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "included-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "their-own-token")
    plan = Plan()
    claude, codex = PlanBrain("claude", plan), PlanBrain("codex", plan)
    e = Engine(tmp_path, claude, brains={"claude": claude, "codex": codex})
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    e.agents_dir.mkdir()
    e.requests_dir.mkdir()
    e.limit_resets = lambda backend: plan.resets.get(backend, "")
    e.plan = plan
    return e


def said(e):
    return [m["text"] for m in e.messages(HOME) if m["role"] == "ken"]


def left_on_credits(e, backend="claude"):
    """What an older Ken did at a plan's limit: switched to Ken AI credits by itself until it resets."""
    e.kv_set("credits-for-now", json.dumps({"from": backend, "until": "22:20"}))
    onboarding.os.environ["KEN_CREDITS_FOR_NOW"] = "1"
    onboarding.use_included_ai()


async def test_a_plan_at_its_limit_says_how_to_keep_going_and_stays_on_their_own(ken):
    ken.plan.limited["claude"] = True
    assert not await ken.send_message(HOME, "draft my post")
    assert len(ken.brain.asks) == 1 and not ken.brain.asks[0]["credits"]
    assert not onboarding.included_ai_in_use() and said(ken) == [LIMIT]
    assert not await ken.send_message(HOME, "another")  # still their own, still not switched
    assert [a["credits"] for a in ken.brain.asks] == [False, False]


async def test_a_chatgpt_plan_at_its_limit_stays_on_chatgpt(ken):
    await ken.set_runtime(HOME, backend="codex")
    ken.plan.limited["codex"] = True
    assert not await ken.send_message(HOME, "draft my post")
    assert ken.backend(HOME) == "codex" and not ken.brains["claude"].asks
    assert said(ken) == [LIMIT.replace("Claude", "ChatGPT")]


async def test_a_switch_an_older_ken_made_goes_back_when_the_plan_resets(ken):
    left_on_credits(ken)
    ken.plan.resets["claude"] = "22:20"  # still at its limit
    assert await ken.send_message(HOME, "hi")
    assert ken.brain.asks[-1]["credits"] and said(ken) == ["answer on credits"]
    ken.plan.resets.clear()  # 22:20 came
    assert await ken.send_message(HOME, "thanks")
    assert not ken.brain.asks[-1]["credits"] and not onboarding.included_ai_in_use()
    assert said(ken)[-2:] == ["Back on your own Claude.", "answer on claude"]


async def test_their_own_pick_of_credits_is_left_alone(ken, monkeypatch):
    monkeypatch.setenv("KEN_USE_CREDITS", "1")
    onboarding.use_included_ai()
    assert await ken.send_message(HOME, "hi")
    assert ken.brain.asks[-1]["credits"] and said(ken) == ["answer on credits"]
    ken.back_from_credits()
    assert onboarding.included_ai_in_use()


async def test_without_ken_ai_credits_the_limit_is_said_as_before(ken, monkeypatch):
    monkeypatch.delenv("KEN_INCLUDED_AI_TOKEN")
    ken.plan.limited["claude"] = True
    assert not await ken.send_message(HOME, "hi")
    assert len(ken.brain.asks) == 1
    assert said(ken) == ["You've reached your Claude plan's limit for now. It resets at 22:20."]


async def test_credits_running_out_say_how_to_go_back_to_their_own(ken, monkeypatch):
    monkeypatch.setenv("KEN_USE_CREDITS", "1")  # they picked Ken AI credits in Settings
    onboarding.use_included_ai()
    ken.plan.resets["claude"] = "22:20"
    ken.plan.credits_out = True
    assert not await ken.send_message(HOME, "hi")
    assert ken.messages(HOME)[-1]["text"] == (
        "You've used your Ken AI credits. Switch back to your own Claude in Settings → AI (its limit resets at 22:20).")
    # An older Ken's switch, and their own plan reset meanwhile: Ken goes back to it by itself.
    monkeypatch.delenv("KEN_USE_CREDITS")
    left_on_credits(ken)
    ken.plan.resets.clear()
    msg, action = ken.explain(AIUnavailable(402, "billing_error"), HOME)
    assert msg == "You've used your Ken AI credits, so I switched back to your own Claude." and action == "retry"
    assert not onboarding.included_ai_in_use()


async def test_scheduled_and_background_jobs_at_the_limit_are_not_run_on_credits(ken):
    ken.plan.limited["claude"] = True
    text = await ken.run_job(HOME, "the brief", job={"name": "morning-brief", "label": "☀️ Morning brief"})
    assert len(ken.brain.asks) == 1 and not onboarding.included_ai_in_use()
    assert "plan's limit" in said(ken)[-1] and "Settings → AI" in said(ken)[-1]
    ken.create_agent("Reader")
    run = ken.start_run("reader", "Read", "read it")
    await ken.run_tasks[run["id"]]
    row = ken._run_row(run["id"])
    assert row["status"] == "failed" and not ken.brain.asks[-1]["credits"] and not onboarding.included_ai_in_use()
