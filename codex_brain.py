"""Codex app-server adapter for Ken's persistent conversations."""
from __future__ import annotations

import asyncio
import os
import time

from brains import Brain, Session, describe_tool

def codex_client(*, cwd=None, env=None):
    from openai_codex import AsyncCodex, CodexConfig

    extra = dict(env or {})
    if os.environ.get("KEN_CODEX_HOME"):
        extra["CODEX_HOME"] = os.environ["KEN_CODEX_HOME"]
    # An explicit API key uses an environment-backed provider, without replacing
    # the user's saved ChatGPT login in the shared Codex credential store.
    overrides = ()
    if extra.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        overrides = (
            'model_provider="ken_openai"',
            'model_providers.ken_openai.name="OpenAI"',
            'model_providers.ken_openai.base_url="https://api.openai.com/v1"',
            'model_providers.ken_openai.env_key="OPENAI_API_KEY"',
            'model_providers.ken_openai.wire_api="responses"',
        )
    return AsyncCodex(CodexConfig(
        codex_bin=os.environ.get("CODEX_BIN") or None,
        cwd=cwd, env=extra, config_overrides=overrides,
        client_name="ken", client_title="Ken Computer",
    ))


def mcp_config(servers: dict) -> dict:
    """Translate Ken's Claude-compatible external MCP configuration to Codex."""
    result = {}
    for name, server in servers.items():
        kind = server.get("type", "stdio" if "command" in server else "http")
        if kind == "stdio":
            result[name] = {k: v for k, v in server.items() if k in ("command", "args", "env", "cwd")}
        elif kind in ("http", "sse"):
            if kind == "sse":
                raise ValueError(f"Codex needs a streamable HTTP or stdio MCP server: {name} uses legacy SSE.")
            result[name] = {"url": server["url"]}
            if server.get("headers"):
                result[name]["http_headers"] = server["headers"]
        else:
            raise ValueError(f"MCP server {name} cannot run in Codex ({kind}). Use stdio or HTTP.")
        result[name]["enabled"] = True
    return result


class CodexSession(Session):
    def __init__(self, brain, cwd, system, resume, model_fn=None, mcp_fn=None, env_fn=None):
        self.brain, self.cwd, self.system = brain, cwd, system
        self.model_fn = model_fn or brain.model_fn
        self.mcp_fn = mcp_fn or brain.mcp_factory
        self.env_fn = env_fn
        self.session_id = resume
        self.client = self.thread = self.turn = None
        self.busy = False
        self.last_activity = time.monotonic()
        self._connecting = asyncio.Lock()

    def _model(self):
        model = self.model_fn()
        # Agents created with Claude may still have a Claude-only model override.
        return model if model and not model.startswith("claude-") else None

    async def warm(self):
        from openai_codex import ApprovalMode, Sandbox

        async with self._connecting:
            if self.thread is not None:
                return
            client = codex_client(cwd=self.cwd, env=self.env_fn() if self.env_fn else {})
            try:
                await client.__aenter__()
                from openai_codex.generated.v2_all import ConfigReadResponse

                # Config tables merge in app-server. An empty mcp_servers table
                # does not disable the user's servers; explicitly disable them
                # before enabling this Ken agent's selected tools.
                inherited = await client._client.request("config/read", {"cwd": self.cwd, "includeLayers": False},
                                                        response_model=ConfigReadResponse)
                servers = {name: {"enabled": False} for name in inherited.config.model_dump().get("mcp_servers", {})}
                servers.update(mcp_config(self.mcp_fn() if self.mcp_fn else {}))
                options = dict(
                    cwd=self.cwd, model=self._model(),
                    developer_instructions=self.system() if callable(self.system) else self.system,
                    sandbox=Sandbox.full_access, approval_mode=ApprovalMode.deny_all,
                    config={"mcp_servers": servers},
                )
                if self.session_id:
                    # Do not silently discard a conversation on an auth/network failure.
                    thread = await client.thread_resume(self.session_id, **options)
                else:
                    thread = await client.thread_start(**options)
                self.client, self.thread, self.session_id = client, thread, thread.id
            except BaseException:
                await client.close()
                raise

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        await self.warm()
        self.busy = True
        self.last_activity = time.monotonic()
        partial, spoken = {}, set()
        final = None
        completed = False
        try:
            self.turn = await self.thread.turn(prompt, model=self._model())
            async for event in self.turn.stream():
                self.last_activity = time.monotonic()
                if not hasattr(event.payload, "model_dump"):
                    continue
                payload = event.payload.model_dump(mode="json", by_alias=True)
                if event.method == "item/agentMessage/delta":
                    key = payload["itemId"]
                    partial[key] = partial.get(key, "") + payload["delta"]
                    if on_delta:
                        await on_delta(partial[key])
                elif event.method in ("item/started", "item/completed"):
                    item = payload["item"]
                    kind = item["type"]
                    if event.method == "item/started" and on_tool:
                        if kind == "commandExecution":
                            await on_tool(describe_tool("Shell", item))
                        elif kind == "mcpToolCall":
                            await on_tool(describe_tool(item["tool"], item.get("arguments", {})))
                        elif kind in ("fileChange", "webSearch", "imageView"):
                            await on_tool(describe_tool(kind, item))
                    if event.method == "item/completed" and kind == "agentMessage":
                        text = item.get("text", "").strip()
                        if text and item["id"] not in spoken:
                            spoken.add(item["id"])
                            final = text
                            await on_text(text)
                elif event.method == "turn/completed":
                    completed = True
                    turn = payload["turn"]
                    if turn["status"] == "failed":
                        raise RuntimeError((turn.get("error") or {}).get("message") or "Codex turn failed")
                    if turn["status"] == "interrupted":
                        raise asyncio.CancelledError
            if not completed:
                raise RuntimeError("Codex disconnected before the turn completed")
            return final
        except asyncio.CancelledError:
            await self.interrupt()
            raise
        except Exception:
            # A side effect may already have happened. Never replay a failed turn.
            await self.close()
            raise
        finally:
            self.busy = False
            self.turn = None

    async def interrupt(self):
        if self.turn is not None:
            try:
                await self.turn.interrupt()
            except Exception:
                await self.close()

    async def close(self):
        async with self._connecting:
            client, self.client = self.client, None
            self.thread = self.turn = None
            self.busy = False
            if client is not None:
                await client.close()


class CodexBrain(Brain):
    name = "codex"

    def __init__(self, model_fn=lambda: "", *, mcp_factory=None):
        self.model_fn, self.mcp_factory = model_fn, mcp_factory

    def session(self, *, cwd, system, resume=None, model_fn=None, mcp_fn=None, env_fn=None):
        return CodexSession(self, cwd, system, resume, model_fn, mcp_fn, env_fn)
