"""Their own Claude or ChatGPT plan at its limit: Ken switches to Ken AI credits by itself until it resets, then back."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import onboarding  # noqa: E402
from brains import AIUnavailable  # noqa: E402
from engine import HOME, Engine  # noqa: E402
from test_engine import FakeBrain, FakeSession  # noqa: E402

SWITCHED = "Your Claude plan hit its limit, so I switched to Ken AI credits until 22:20. I'll switch back then."


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


async def test_a_plan_at_its_limit_switches_to_credits_and_back_when_it_resets(ken):
    ken.plan.limited["claude"] = True
    assert await ken.send_message(HOME, "draft my post")
    assert [a["credits"] for a in ken.brain.asks] == [False, True]
    assert said(ken) == [SWITCHED, "answer on credits"]
    assert await ken.send_message(HOME, "another")  # still on credits, nothing said again
    assert said(ken)[-1] == "answer on credits" and len(said(ken)) == 3
    ken.plan.resets.clear()  # 22:20 came
    ken.plan.limited.clear()
    assert await ken.send_message(HOME, "thanks")
    assert not ken.brain.asks[-1]["credits"] and not onboarding.included_ai_in_use()
    assert said(ken)[-2:] == ["Back on your own Claude.", "answer on claude"]
    assert await ken.send_message(HOME, "more")
    assert said(ken)[-1] == "answer on claude"  # said once


async def test_a_reply_that_had_started_acting_is_not_run_again(ken):
    ken.plan.limited["claude"], ken.plan.act_first = True, True
    assert not await ken.send_message(HOME, "email Sam")
    assert len(ken.brain.asks) == 1 and onboarding.included_ai_in_use()  # switched, not retried
    assert said(ken) == ["Your Claude plan hit its limit, so I switched to Ken AI credits until 22:20. "
                         "Tell me to carry on and I'll pick up where I stopped."]


async def test_a_chatgpt_plan_at_its_limit_moves_the_chat_to_claude_on_credits_and_back(ken):
    await ken.set_runtime(HOME, backend="codex")
    ken.plan.limited["codex"] = True
    assert await ken.send_message(HOME, "draft my post")
    assert ken.brains["claude"].asks[-1]["credits"] and ken.backend(HOME) == "claude"
    assert said(ken)[0].startswith("Your ChatGPT plan hit its limit, so I switched to Ken AI credits until 22:20.")
    ken.plan.resets.clear()
    ken.plan.limited.clear()
    assert await ken.send_message(HOME, "thanks")
    assert ken.backend(HOME) == "codex" and said(ken)[-2:] == ["Back on your own ChatGPT.", "answer on codex"]


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


async def test_credits_running_out_say_how_to_go_back_to_their_own(ken):
    ken.plan.limited["claude"] = True
    ken.plan.credits_out = True
    assert not await ken.send_message(HOME, "hi")
    assert ken.messages(HOME)[-1]["text"] == (
        "You've used your Ken AI credits. Switch back to your own Claude in Settings → AI (its limit resets at 22:20).")
    # Their own plan reset meanwhile: Ken goes back to it by itself.
    ken.plan.resets.clear()
    ken.plan.limited.clear()
    msg, action = ken.explain(AIUnavailable(402, "billing_error"), HOME)
    assert msg == "You've used your Ken AI credits, so I switched back to your own Claude." and action == "retry"
    assert not onboarding.included_ai_in_use()


async def test_scheduled_and_background_jobs_switch_and_run_once_more(ken):
    ken.plan.limited["claude"] = True
    text = await ken.run_job(HOME, "the brief", job={"name": "morning-brief", "label": "☀️ Morning brief"})
    assert text == f"{SWITCHED}\n\nanswer on credits"
    ken.back_from_credits(force=True)
    ken.create_agent("Reader")
    run = ken.start_run("reader", "Read", "read it")
    await ken.run_tasks[run["id"]]
    assert ken._run_row(run["id"])["status"] == "done" and ken.brain.asks[-1]["credits"]
