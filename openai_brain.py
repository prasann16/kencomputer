"""An OpenAI-compatible brain: any endpoint that speaks the OpenAI chat API.

Set it up in Ken's setup screen (or Settings → AI) with its address, key and model; they're kept in
~/.ken/.env as KEN_OPENAI_BASE_URL, KEN_OPENAI_API_KEY and KEN_OPENAI_MODEL. Ken then offers it beside
Claude and Codex, e.g. OpenAI itself, OpenRouter, Ollama, LM Studio or vLLM.

It writes text only: the MCP tools the other brains get aren't passed on, so work that needs them
belongs on Claude or Codex.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from brains import AIUnavailable, Brain, Session

BASE_URL, API_KEY, MODEL = "KEN_OPENAI_BASE_URL", "KEN_OPENAI_API_KEY", "KEN_OPENAI_MODEL"
KEEP = 40  # messages kept per conversation: enough context, bounded so a long chat can't overflow the model


def configured() -> bool:
    """An endpoint was set up, so this brain is worth offering."""
    return bool(os.environ.get(BASE_URL))


def client(url: str = "", key: str = ""):
    """The SDK client for that endpoint (imported here: Ken runs without the openai package until it's used)."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=url or os.environ[BASE_URL], api_key=key or os.environ.get(API_KEY) or "unused")


async def models(url: str = "", key: str = "") -> list[str]:
    """The endpoint's model ids, for the model menu (or to check an address before saving it)."""
    c = client(url, key)
    try:
        return [m.id for m in (await c.models.list()).data]
    finally:
        await c.close()


class OpenAISession(Session):
    """One conversation, kept as its messages on disk so a restart carries on where it left off."""

    def __init__(self, brain: OpenAIBrain, cwd: str, system, resume: str | None, model_fn=None) -> None:
        self.brain, self.cwd, self.system = brain, cwd, system
        self.model_fn = model_fn or brain.model_fn
        self.session_id = resume or uuid.uuid4().hex
        self.messages = self._saved()
        self.client = None
        self.busy = False
        self.last_activity = time.monotonic()
        self.usage: list[dict] = []
        self.context_tokens = 0

    def _file(self) -> Path:
        return self.brain.home / "openai" / f"{self.session_id}.json"

    def _saved(self) -> list[dict]:
        try:
            messages = json.loads(self._file().read_text())
        except (OSError, ValueError):
            return []
        return messages if isinstance(messages, list) else []

    def _save(self) -> None:
        try:
            self._file().parent.mkdir(parents=True, exist_ok=True)
            self._file().write_text(json.dumps(self.messages))
        except OSError:
            pass  # the answer stands; only its history is lost

    @staticmethod
    def _row(model: str, used) -> list[dict]:
        """What the call used, the way Ken counts the other brains' usage (no prices: the endpoint sets its own)."""
        cached = getattr(getattr(used, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        return [{"model": model, "cost_usd": None,
                 "input_tokens": max(0, (used.prompt_tokens or 0) - cached),
                 "output_tokens": used.completion_tokens or 0,
                 "cache_read_tokens": cached, "cache_write_tokens": 0}]

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        from openai import OpenAIError

        self.busy, self.heard, self.usage = True, False, []
        system = self.system() if callable(self.system) else self.system
        messages = ([{"role": "system", "content": system}] if system else []) + self.messages + [
            {"role": "user", "content": prompt}]
        model = self.model_fn() or os.environ.get(MODEL, "")
        text = ""
        try:
            self.client = self.client or self.brain.client()
            stream = await self.client.chat.completions.create(model=model, messages=messages, stream=True)
            async for chunk in stream:
                self.heard, self.last_activity = True, time.monotonic()
                if chunk.usage:
                    self.usage = self._row(model, chunk.usage)
                    self.context_tokens = chunk.usage.prompt_tokens or 0
                delta = chunk.choices[0].delta.content if chunk.choices else ""
                if not delta:
                    continue
                text += delta
                if on_delta is not None:
                    await on_delta(text)
        except OpenAIError as e:
            raise AIUnavailable(getattr(e, "status_code", None), str(e)) from e
        finally:
            self.busy = False
        text = text.strip()
        if text:
            self.messages = (self.messages + [{"role": "user", "content": prompt},
                                              {"role": "assistant", "content": text}])[-KEEP:]
            self._save()
            await on_text(text)
        return text or None

    async def interrupt(self) -> None:
        await self.close()  # closing the stream ends the request in flight

    async def close(self) -> None:
        self.busy = False
        client, self.client = self.client, None
        if client is not None:
            await client.close()


class OpenAIBrain(Brain):
    name = "openai"
    client = staticmethod(client)

    def __init__(self, home: Path, model_fn=lambda: "") -> None:
        self.home, self.model_fn, self.on_rate_limit = Path(home), model_fn, None

    def session(self, *, cwd, system, resume=None, model_fn=None, mcp_fn=None, env_fn=None) -> Session:
        return OpenAISession(self, cwd, system, resume, model_fn)
