"""Brains — the model that does the thinking, behind one small interface.

The engine never talks to a model SDK directly: it asks a Brain for a Session
and feeds it prompts. Claude Code is the first brain; another model or agent
runtime plugs in by implementing Brain.session() and the Session methods.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger("ken")

OnText = Callable[[str], Awaitable[None]]
OnTool = Callable[[str], Awaitable[None]]


class Session:
    """One ongoing conversation with a brain, working in one folder."""

    session_id: str | None = None
    busy: bool = False

    async def ask(
        self, prompt: str, on_text: OnText, on_tool: OnTool | None = None, on_delta: OnText | None = None
    ) -> str | None:
        """Send a prompt; stream each finished utterance to on_text, each action
        to on_tool, and the utterance being written so far to on_delta.
        Returns the final text, or None if nothing was said."""
        raise NotImplementedError

    async def warm(self) -> None:
        """Get ready to answer (connect, spawn a process) before the first prompt."""

    async def interrupt(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class Brain:
    name = "base"

    def session(
        self,
        *,
        cwd: str,
        system: str | Callable[[], str],
        resume: str | None = None,
    ) -> Session:
        raise NotImplementedError


def describe_tool(name: str, args: dict) -> str:
    """One human-readable line for an agent action: 'Bash · npm test'."""
    for key in ("description", "command", "file_path", "path", "pattern", "url", "query", "prompt"):
        val = args.get(key) if isinstance(args, dict) else None
        if isinstance(val, str) and val.strip():
            detail = " ".join(val.split())
            if key == "description":
                return detail[:110]  # already written for humans: "Run the test suite"
            if key == "file_path" or key == "path":
                detail = detail.rsplit("/", 1)[-1] or detail
            return f"{name} · {detail[:110]}"
    return name


class ClaudeSession(Session):
    """A persistent Claude Code session (Agent SDK): no per-message cold start."""

    def __init__(self, brain: "ClaudeBrain", cwd: str, system, resume: str | None) -> None:
        self.brain = brain
        self.cwd = cwd
        self.system = system
        self.session_id = resume
        self.client = None
        self.model = ""
        self.busy = False
        self._connecting = asyncio.Lock()

    async def _connect(self, resume: str | None) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        system = self.system() if callable(self.system) else self.system
        model = self.brain.model_fn()
        options = ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code", "append": system},
            permission_mode=self.brain.permission_mode,
            can_use_tool=self.brain.can_use_tool,
            mcp_servers=self.brain.mcp_factory() if self.brain.mcp_factory else {},
            include_partial_messages=True,
            cwd=self.cwd,
            model=model or None,
            resume=resume,
        )
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()
        self.model = model

    async def _ensure(self) -> None:
        async with self._connecting:
            await self._ensure_locked()

    async def _ensure_locked(self) -> None:
        if self.client is not None:
            return
        try:
            await self._connect(self.session_id)
        except Exception as e:
            self.client = None
            if not self.session_id:
                raise
            log.warning("resume failed (%s) — starting fresh", e)
            self.session_id = None
            await self._connect(None)

    async def warm(self) -> None:
        await self._ensure()

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock

        await self._ensure()
        model = self.brain.model_fn()
        if model != self.model:
            await self.client.set_model(model or None)
            self.model = model
        self.busy = True
        last_text = ""
        final = None
        partial = ""
        try:
            await self.client.query(prompt)
            async for msg in self.client.receive_response():
                if isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    if ev.get("type") == "message_start":
                        partial = ""
                    elif ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                        partial += ev["delta"].get("text", "")
                        if on_delta is not None and msg.parent_tool_use_id is None:
                            await on_delta(partial)
                    continue
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock) and on_tool is not None:
                            await on_tool(describe_tool(block.name, block.input))
                    text = "\n".join(
                        b.text for b in msg.content if isinstance(b, TextBlock) and b.text
                    ).strip()
                    if text and text != last_text:
                        last_text = final = text
                        await on_text(text)
                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        self.session_id = msg.session_id
                    result = (msg.result or "").strip()
                    if result and result != last_text:
                        final = result
                        await on_text(result)
        finally:
            self.busy = False
        return final

    async def interrupt(self) -> None:
        if self.client is not None:
            await self.client.interrupt()

    async def close(self) -> None:
        # Clear/quit may arrive while a background warm-up is still connecting.
        # Finish that connection before disconnecting it, so no CLI is orphaned.
        async with self._connecting:
            client, self.client = self.client, None
            self.busy = False
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass


class ClaudeBrain(Brain):
    name = "claude"

    def __init__(self, model_fn: Callable[[], str] = lambda: "", *, permission_mode="bypassPermissions", can_use_tool=None, mcp_factory=None) -> None:
        self.model_fn = model_fn
        self.permission_mode = permission_mode
        self.can_use_tool = can_use_tool
        self.mcp_factory = mcp_factory

    def session(self, *, cwd, system, resume=None) -> Session:
        return ClaudeSession(self, cwd, system, resume)
