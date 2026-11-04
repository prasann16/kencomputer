"""Brains — the model that does the thinking, behind one small interface.

The engine never talks to a model SDK directly: it asks a Brain for a Session
and feeds it prompts. Claude Code is the first brain; another model or agent
runtime plugs in by implementing Brain.session() and the Session methods.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable

log = logging.getLogger("ken")
# Claude Code's error when a model is newer than it supports ("...does not support this model;
# version X or newer is required"). Ken retries on the default model instead of showing it.
NEEDS_NEWER_CLI = "does not support this model"
TOO_NEW = object()
# When the AI service fails, the brain's own runtime retries for minutes while Ken looks like it's thinking.
# Ken stops at once on an answer that won't change (a refused key, no credit) and after RETRY_LIMIT seconds
# of retrying anything else (busy, down, unreachable), and says so.
FINAL_STATUSES = {400, 401, 402, 403, 404}
RETRY_LIMIT = 20


class AIUnavailable(RuntimeError):
    """The AI service didn't answer: `status` is its HTTP status (None when it couldn't be reached)."""

    def __init__(self, status: int | None, error: str = ""):
        self.status, self.error = status, error or ""
        super().__init__(f"AI request failed ({status or 'no connection'}): {error}")


def give_up(status: int | None, error: str, retrying_for: float, next_wait: float) -> bool:
    """Whether to stop retrying an AI request now rather than keep the person waiting."""
    final = status in FINAL_STATUSES or error in ("authentication_failed", "billing_error", "invalid_request")
    return final or retrying_for + next_wait > RETRY_LIMIT

OnText = Callable[[str], Awaitable[None]]
OnTool = Callable[[str], Awaitable[None]]


class Session:
    """One ongoing conversation with a brain, working in one folder."""

    session_id: str | None = None
    busy: bool = False
    last_activity: float = 0.0  # monotonic time the brain was last heard from; the engine stops a silent one
    heard: bool = False  # whether this ask got anything from the AI yet; the engine stops one that never answers
    # What the last ask used, one row per model: model, cost_usd (None when the brain can't price it)
    # and token counts. Empty when the brain didn't say.
    usage: list[dict] = []

    async def ask(
        self, prompt: str, on_text: OnText, on_tool: OnTool | None = None, on_delta: OnText | None = None
    ) -> str | None:
        """Send a prompt; stream each finished utterance to on_text, each action
        to on_tool, and the utterance being written so far to on_delta.
        Returns the final text, or None if nothing was said."""
        raise NotImplementedError

    async def warm(self) -> None:
        """Get ready to answer (connect, spawn a process) before the first prompt."""

    async def steer(self, prompt: str) -> bool:
        """Add a message to the turn that's running, the way Claude Code takes messages while it works.
        False when there's no turn it can join; the engine then answers it after this one."""
        return False

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
        model_fn: Callable[[], str] | None = None,
        mcp_fn: Callable[[], dict] | None = None,
        env_fn: Callable[[], dict] | None = None,
    ) -> Session:
        raise NotImplementedError


# What a step looks like to the person: plain words, never a command line. Ken's people aren't developers, and the
# same line shows in the app, in Telegram and on the Mac, so it's made here once.
_SHELL_WORDS = [  # (any of these in the command, what it means), first match wins
    (("--video-render",), "Rendering your video"),
    (("--video-stills",), "Checking how the video looks"),
    (("--video-cut", "ffmpeg", "ffprobe"), "Editing your video"),
    (("transcribe",), "Transcribing"),
    (("remotion", "npx remotion"), "Rendering your video"),
    (("curl ", "wget ", "http://", "https://"), "Checking online"),
    (("pip ", "npm install", "npm i ", "uv pip", "apt-get"), "Installing what it needs"),
    (("git ",), "Updating files"),
    (("ls ", "find ", "cat ", "head ", "tail ", "grep "), "Looking through files"),
    (("rm ", "mv ", "cp ", "mkdir "), "Tidying up files"),
]
_TOOL_WORDS = {
    "Read": "Reading", "Write": "Writing", "Edit": "Editing", "MultiEdit": "Editing", "NotebookEdit": "Editing",
    "Grep": "Looking through files", "Glob": "Looking through files", "LS": "Looking through files",
    "WebSearch": "Searching the web", "WebFetch": "Reading a web page", "Task": "Getting help with part of this",
    "Agent": "Getting help with part of this", "TodoWrite": "Planning the steps", "Skill": "Using a skill",
    "transcribe_media": "Transcribing",
}


def describe_tool(name: str, args: dict) -> str:
    """One plain line for an agent action: 'Editing your video', 'Reading invoice.pdf', 'Searching the web for …'."""
    args = args if isinstance(args, dict) else {}
    text = lambda key: " ".join(str(args.get(key) or "").split())
    if text("description") and name not in ("Bash", "Shell", "shell", "local_shell", "exec_command"):
        return text("description")[:110]  # already written for people: "Run the test suite"
    if name in ("Bash", "Shell", "shell", "local_shell", "exec_command", "command_execution"):
        raw = args.get("command")
        command = " ".join(map(str, raw)) if isinstance(raw, list) else text("command") or text("cmd")
        low = f" {command.lower()} "
        for needles, words in _SHELL_WORDS:
            if any(n in low for n in needles):
                return words
        return "Working on it"
    if name.startswith("mcp__"):  # mcp__apps__GMAIL_SEND_EMAIL -> "Using Gmail"
        tool = name.split("__")[-1]
        app = tool.split("_")[0].capitalize() if tool.isupper() else ""
        return f"Using {app}" if app else "Using one of your apps"
    words = _TOOL_WORDS.get(name)
    if words is None:
        return "Working on it"
    for key in ("file_path", "path"):
        if text(key):
            return f"{words} {text(key).rsplit('/', 1)[-1][:80]}"
    if name == "WebSearch" and text("query"):
        return f"{words} for {text('query')[:80]}"
    if name == "WebFetch" and text("url"):
        from urllib.parse import urlparse
        host = urlparse(text("url")).netloc
        return f"Reading {host}" if host else words
    if name == "Skill" and text("skill"):
        return f"Using its {text('skill').split(':')[-1].replace('-', ' ')} skill"
    return words


def spent_since(now: dict | None, before: dict | None) -> list[dict]:
    """What each model used between two of Claude Code's running totals (its modelUsage, which keeps
    counting across messages and resumes), one row per model that used anything."""
    rows = []
    for model, u in (now or {}).items():
        b = (before or {}).get(model) or {}
        if (u.get("costUSD") or 0) < (b.get("costUSD") or 0):
            b = {}  # Claude Code started counting again
        diff = lambda k: max(0, (u.get(k) or 0) - (b.get(k) or 0))
        row = {"model": model, "cost_usd": round(diff("costUSD"), 6),
               "input_tokens": diff("inputTokens"), "output_tokens": diff("outputTokens"),
               "cache_read_tokens": diff("cacheReadInputTokens"), "cache_write_tokens": diff("cacheCreationInputTokens")}
        if any(v for k, v in row.items() if k != "model"):
            rows.append(row)
    return rows


class ClaudeSession(Session):
    """A persistent Claude Code session (Agent SDK): no per-message cold start."""

    def __init__(self, brain: "ClaudeBrain", cwd: str, system, resume: str | None, model_fn=None, mcp_fn=None, env_fn=None) -> None:
        self.brain = brain
        self.model_fn = model_fn or brain.model_fn  # an agent may choose its own model
        self.mcp_fn = mcp_fn or brain.mcp_factory  # and its own tools
        self.env_fn = env_fn  # and its own keys, as environment variables
        self.cwd = cwd
        self.system = system
        self.session_id = resume
        self.client = None
        self.model = ""
        self.busy = False
        self.last_activity = time.monotonic()
        self.usage: list[dict] = []
        # Claude Code's running totals so far, to tell what each message added. A resumed session's
        # totals include its past, so they're unknown (None) until the engine passes in the saved ones.
        self.spent: dict | None = None if resume else {}
        self.stderr: deque[str] = deque(maxlen=40)  # Claude Code's own error output, logged when a turn fails
        self._heard = False
        self._connecting = asyncio.Lock()
        # Messages sent into the running turn that Claude Code hasn't taken in yet (it echoes each one it takes).
        self._waiting: list[str] = []
        self._stopping = self._ended = False

    async def _connect(self, resume: str | None) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        system = self.system() if callable(self.system) else self.system
        model = self.model_fn()
        options = ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code", "append": system},
            permission_mode=self.brain.permission_mode,
            can_use_tool=self.brain.can_use_tool,
            mcp_servers=self.mcp_fn() if self.mcp_fn else {},
            # No Claude Code background helpers: their results arrive after Ken's reply has ended and are
            # lost. Long work goes to Ken's own background jobs, which report back (engine.start_run).
            env={**(self.env_fn() if self.env_fn else {}), "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
            include_partial_messages=True,
            cwd=self.cwd,
            model=model or None,
            resume=resume,
            # One message can carry a browser screenshot or a page snapshot; the SDK's 1 MB default
            # dropped the whole turn ("JSON message exceeded maximum buffer size").
            max_buffer_size=64 * 1024 * 1024,
            stderr=self.stderr.append,
            # Ken is the same on every computer: none of this Mac's own Claude Code settings,
            # plugins, CLAUDE.md or MCP servers, only the tools Ken passes in.
            setting_sources=[],
            strict_mcp_config=True,
            plugins=self.brain.plugins,  # Ken's skills (~/.ken/skills), the same in every chat
            # Echo each message as Claude Code takes it in, so Ken knows when a message sent mid-turn
            # was answered in this turn or still has a turn of its own coming.
            extra_args={"replay-user-messages": None},
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
            self.session_id, self.spent = None, {}
            await self._connect(None)

    async def warm(self) -> None:
        await self._ensure()

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        from claude_agent_sdk import CLIConnectionError, ProcessError

        try:
            return await self._ask_once(prompt, on_text, on_tool, on_delta)
        except (CLIConnectionError, ProcessError) as e:
            # Claude Code's process was gone before it said or did anything (it was killed, or
            # died between messages): start it again and ask once more. Never after it started
            # working, since it may already have sent or posted something.
            if self._heard:
                raise
            log.warning("Claude Code wasn't running (%s); starting it again", e)
            await self.close()
            return await self._ask_once(prompt, on_text, on_tool, on_delta)

    async def _ask_once(self, prompt, on_text, on_tool=None, on_delta=None):
        final = await self._ask(prompt, on_text, on_tool, on_delta)
        if final is TOO_NEW:  # the model needs a newer Claude Code: answer on the default instead
            log.warning("model %s needs a newer Claude Code; using the default until Ken updates", self.model)
            self.brain.unsupported.add(self.model)
            final = await self._ask(prompt, on_text, on_tool, on_delta)
        return None if final is TOO_NEW else final

    async def _ask(self, prompt, on_text, on_tool=None, on_delta=None):
        import claude_agent_sdk
        from claude_agent_sdk import (AssistantMessage, ResultMessage, StreamEvent, SystemMessage, TextBlock, ToolUseBlock,
                                      UserMessage)

        RateLimitEvent = getattr(claude_agent_sdk, "RateLimitEvent", ())  # SDKs before 0.2.16x don't have it

        await self._ensure()
        model = self.model_fn()
        if model in self.brain.unsupported:
            model = ""
        if model != self.model:
            await self.client.set_model(model or None)
            self.model = model
        self.busy, self._heard, self.heard = True, False, False
        self._waiting, self._stopping, self._ended = [], False, False
        retrying, failed = None, None  # when Claude Code started retrying the AI, and why Ken stopped it
        self.usage = []
        self.last_activity = time.monotonic()
        last_text = ""
        final = None
        partial = ""
        try:
            await self.client.query(prompt)
            async for msg in self._messages():
                if isinstance(msg, SystemMessage) and msg.subtype == "api_retry":
                    # The AI service failed and Claude Code is about to try again: none of this is progress.
                    status, error = msg.data.get("error_status"), str(msg.data.get("error") or "")
                    retrying = retrying or time.monotonic()
                    if failed is None and give_up(status, error, time.monotonic() - retrying,
                                                  (msg.data.get("retry_delay_ms") or 0) / 1000):
                        log.warning("AI request failed (%s %s); stopping instead of retrying", status, error)
                        failed = AIUnavailable(status, error)
                        await self.client.interrupt()
                    continue
                if not (isinstance(msg, SystemMessage) and msg.subtype in ("init", "status")):
                    self._heard = self.heard = True
                    self.last_activity = time.monotonic()
                # Save the session as soon as it exists, not only when a long
                # task finishes. An interrupted attempt can then resume too.
                sid = msg.data.get('session_id') if isinstance(msg, SystemMessage) else getattr(msg, 'session_id', None)
                if sid:
                    self.session_id = sid
                if isinstance(msg, RateLimitEvent):  # the plan's usage changed: Ken keeps it for /usage and alerts
                    if self.brain.on_rate_limit is not None:
                        info = msg.rate_limit_info
                        self.brain.on_rate_limit(info.raw or {"status": info.status, "resetsAt": info.resets_at,
                                                              "rateLimitType": info.rate_limit_type, "utilization": info.utilization})
                    continue
                if isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    if ev.get("type") == "message_start":
                        partial = ""
                    elif ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                        partial += ev["delta"].get("text", "")
                        if on_delta is not None and msg.parent_tool_use_id is None:
                            await on_delta(partial)
                    continue
                if isinstance(msg, UserMessage):
                    said = msg.content if isinstance(msg.content, str) else "\n".join(
                        b.text for b in msg.content if isinstance(b, TextBlock))
                    if msg.parent_tool_use_id is None and said in self._waiting:
                        self._waiting.remove(said)
                        if self._stopping:
                            await self.client.interrupt()  # they stopped: a message sent mid-turn starts no new work
                    continue
                if isinstance(msg, AssistantMessage):
                    if getattr(msg, "error", None):
                        continue  # Claude Code's own "API Error: …" line: the turn fails and Ken says it in plain words
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock) and on_tool is not None:
                            await on_tool(describe_tool(block.name, block.input))
                    text = "\n".join(
                        b.text for b in msg.content if isinstance(b, TextBlock) and b.text
                    ).strip()
                    if NEEDS_NEWER_CLI in text:
                        final = TOO_NEW
                        continue
                    if text and text != last_text:
                        last_text = final = text
                        await on_text(text)
                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        self.session_id = msg.session_id
                    if msg.model_usage is not None:
                        if self.spent is not None:
                            self.usage += spent_since(msg.model_usage, self.spent)
                        self.spent = msg.model_usage
                    result = (msg.result or "").strip()
                    if failed is not None:
                        raise failed
                    if NEEDS_NEWER_CLI in result or final is TOO_NEW:
                        final = TOO_NEW
                    elif msg.is_error and getattr(msg, 'terminal_reason', None) not in {'aborted_streaming', 'aborted_tools'}:
                        raise RuntimeError(result or f"Claude ended without completing the turn ({msg.subtype}).")
                    elif result and result != last_text:
                        final = result
                        await on_text(result)
                    # A message they sent near the end gets a turn of its own: keep reading until it's answered.
                    if not self._waiting:
                        self.busy = False
                        break
                    self._ended = True
        except Exception:
            if self.stderr:
                log.warning("Claude Code said:\n%s", "\n".join(self.stderr))
            raise
        finally:
            self.busy = False
        return final

    async def _messages(self):
        """Claude Code's messages for this ask. Between a turn's end and the turn of a message still waiting,
        give up after 30 seconds of silence, so a message Claude Code dropped never leaves Ken waiting."""
        stream = self.client.receive_messages().__aiter__()
        while True:
            try:
                msg = await (asyncio.wait_for(anext(stream), 30) if self._ended else anext(stream))
            except (StopAsyncIteration, asyncio.TimeoutError):
                self._waiting.clear()
                return
            self._ended = False
            yield msg

    async def steer(self, prompt: str) -> bool:
        if self.client is None or not self.busy or self._stopping:
            return False
        self._waiting.append(prompt)
        try:
            await self.client.query(prompt)
        except Exception:
            self._waiting.remove(prompt)
            return False
        return True

    async def interrupt(self) -> None:
        if self.client is not None:
            self._stopping = self.busy
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
        self.unsupported: set[str] = set()  # models the installed Claude Code rejected
        self.permission_mode = permission_mode
        self.can_use_tool = can_use_tool
        self.mcp_factory = mcp_factory
        self.plugins: list[dict] = []
        self.on_rate_limit: Callable[[dict], None] | None = None  # every session's plan usage readings

    def session(self, *, cwd, system, resume=None, model_fn=None, mcp_fn=None, env_fn=None) -> Session:
        return ClaudeSession(self, cwd, system, resume, model_fn, mcp_fn, env_fn)


async def claude_models() -> list[str]:
    """Ask the signed-in runtime when its saved OAuth token cannot list models directly."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    async with ClaudeSDKClient(options=ClaudeAgentOptions(setting_sources=[], strict_mcp_config=True)) as client:
        info = await client.get_server_info() or {}
        models = [m.get("resolvedModel") or m.get("value") for m in info.get("models", [])]
        return list(dict.fromkeys(m for m in models if m and m != "default"))
