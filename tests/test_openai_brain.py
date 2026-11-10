"""The OpenAI-compatible brain: one streaming answer, history kept, usage counted."""
import asyncio
import os
from types import SimpleNamespace as NS

import openai_brain


class FakeClient:
    def __init__(self, *chunks):
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))
        self.chunks = list(chunks)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)

        async def stream():
            for chunk in self.chunks:
                yield chunk

        return stream()

    async def close(self):
        pass


def chunk(text=None, usage=None):
    return NS(choices=[NS(delta=NS(content=text))] if text is not None else [], usage=usage)


def test_one_ask_streams_text_keeps_history_and_counts_tokens(tmp_path):
    client = FakeClient(chunk("Hello"), chunk(", world"),
                        chunk(usage=NS(prompt_tokens=12, completion_tokens=4,
                                       prompt_tokens_details=NS(cached_tokens=2))))
    brain = openai_brain.OpenAIBrain(tmp_path)
    brain.client = lambda: client
    seen = []

    async def note(t):
        seen.append(t)

    session = brain.session(cwd=str(tmp_path), system="be brief", model_fn=lambda: "test-model")

    text = asyncio.run(session.ask("hi", note, on_delta=note))

    assert text == "Hello, world" and seen[-1] == seen[-2] == "Hello, world"  # deltas, then the whole answer once
    assert client.calls[0]["model"] == "test-model" and client.calls[0]["stream"] is True
    assert client.calls[0]["messages"] == [{"role": "system", "content": "be brief"},
                                           {"role": "user", "content": "hi"}]
    assert session.usage == [{"model": "test-model", "cost_usd": None, "input_tokens": 10, "output_tokens": 4,
                              "cache_read_tokens": 2, "cache_write_tokens": 0}]
    # a fresh session on the same id carries the conversation on after a restart
    again = brain.session(cwd=str(tmp_path), system="", resume=session.session_id)
    assert [m["content"] for m in again.messages] == ["hi", "Hello, world"]


def test_connecting_an_endpoint_saves_it_and_picks_a_model(tmp_path, monkeypatch):
    import pytest

    import onboarding

    async def fake_models(url, key):
        assert (url, key) == ("http://localhost:11434/v1", "")
        return ["llama-3"]

    monkeypatch.setattr(openai_brain, "models", fake_models)
    connected = []

    async def note():
        connected.append(True)

    provider = onboarding.OpenAIOnboarding(tmp_path, note)
    assert asyncio.run(provider.connect(" http://localhost:11434/v1/ ", "", ""))["connected"]
    assert connected and "KEN_OPENAI_BASE_URL=http://localhost:11434/v1\n" in (tmp_path / ".env").read_text()
    assert "KEN_OPENAI_MODEL=llama-3" in (tmp_path / ".env").read_text()  # the first one it offered
    for name in ("KEN_OPENAI_BASE_URL", "KEN_OPENAI_API_KEY", "KEN_OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
        os.environ.pop(name, None)
    with pytest.raises(ValueError):
        asyncio.run(provider.connect("localhost:11434", "", ""))
