"""Brains — the models that do the thinking, behind one small interface.

The engine never talks to a model SDK directly: it asks a Brain for a Session
and feeds it prompts. Claude Code and Codex are the brains today, and one Ken
can use both: every chat runs on the brain that owns the model it's set to
(Brains routes). Another model or agent runtime plugs in by subclassing Brain
(how it signs in, which models it has, its Session) and joining the list in bot.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("ken")
# Claude Code's error when a model is newer than it supports ("...does not support this model;
# version X or newer is required"). Ken retries on the default model instead of showing it.
NEEDS_NEWER_CLI = "does not support this model"
TOO_NEW = object()

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


@dataclass(frozen=True)
class Login:
    """How a brain's own CLI signs in. Onboarding runs it in a terminal and reads it."""

    argv: list[str]  # sign in with a browser on this computer
    device: list[str]  # sign in from another device: a server, Telegram
    url: re.Pattern  # the sign-in page the CLI prints
    code: re.Pattern | None = None  # a one-time code to type on that page (device sign-in)
    paste: bool = False  # the page shows a code the user pastes back into the CLI
    token: re.Pattern | None = None  # a token the CLI prints, saved to .env as env_key
    env_key: str = ""


class Brain:
    name = "base"
    label = "Base"  # what people know it as: the account they sign in with

    def session(
        self,
        *,
        cwd: str,
        system: str | Callable[[], str],
        resume: str | None = None,
        model_fn: Callable[[], str] | None = None,
        recap: Callable[[], str] | None = None,
    ) -> Session:
        """recap: the chat's recent messages, for when it moves here from another brain."""
        raise NotImplementedError

    def connected(self) -> bool:
        """Signed in, so a session can answer."""
        return True

    def forget(self) -> None:
        """A sign-in just finished: don't answer connected() from anything cached."""

    async def models(self) -> list[tuple[str, str]]:
        """The account's live model list, newest first: (id, display name)."""
        return []

    def default_model(self, ids: list[str]) -> str:
        """The model to use when none is chosen; '' lets the brain pick."""
        return ""

    def owns(self, model: str) -> bool:
        return False

    def login(self) -> Login | None:
        return None


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

    def __init__(self, brain: "ClaudeBrain", cwd: str, system, resume: str | None, model_fn=None) -> None:
        self.brain = brain
        self.model_fn = model_fn or brain.model_fn  # a project may choose its own model
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
        model = self.model_fn()
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
        final = await self._ask(prompt, on_text, on_tool, on_delta)
        if final is TOO_NEW:  # the model needs a newer Claude Code: answer on the default instead
            log.warning("model %s needs a newer Claude Code; using the default until Ken updates", self.model)
            self.brain.unsupported.add(self.model)
            final = await self._ask(prompt, on_text, on_tool, on_delta)
        return None if final is TOO_NEW else final

    async def _ask(self, prompt, on_text, on_tool=None, on_delta=None):
        from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock

        await self._ensure()
        model = self.model_fn()
        if model in self.brain.unsupported:
            model = ""
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
                    if NEEDS_NEWER_CLI in text:
                        final = TOO_NEW
                        continue
                    if text and text != last_text:
                        last_text = final = text
                        await on_text(text)
                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        self.session_id = msg.session_id
                    result = (msg.result or "").strip()
                    if NEEDS_NEWER_CLI in result or final is TOO_NEW:
                        final = TOO_NEW
                        continue
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


def claude_oauth_token() -> str:
    """Claude Code's own credentials: env var, credentials file, or macOS Keychain."""
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if tok:
        return tok
    creds = Path.home() / ".claude" / ".credentials.json"
    if creds.exists():
        try:
            return json.loads(creds.read_text())["claudeAiOauth"]["accessToken"]
        except Exception:
            pass
    try:
        import subprocess

        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return json.loads(out.stdout)["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    return ""


class ClaudeBrain(Brain):
    name = "claude"
    label = "Claude"

    def __init__(self, model_fn: Callable[[], str] = lambda: "", *, permission_mode="bypassPermissions", can_use_tool=None, mcp_factory=None) -> None:
        self.model_fn = model_fn
        self.unsupported: set[str] = set()  # models the installed Claude Code rejected
        self.permission_mode = permission_mode
        self.can_use_tool = can_use_tool
        self.mcp_factory = mcp_factory

    def session(self, *, cwd, system, resume=None, model_fn=None, recap=None) -> Session:
        return ClaudeSession(self, cwd, system, resume, model_fn)

    @staticmethod
    def cli() -> str:
        """The Claude CLI that ships with the Agent SDK, else one on PATH."""
        try:
            import claude_agent_sdk

            for name in ("claude", "claude.exe"):
                bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
                if bundled.exists():
                    return str(bundled)
        except ImportError:
            pass
        return shutil.which("claude") or "claude"

    def connected(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or claude_oauth_token())

    async def models(self) -> list[tuple[str, str]]:
        """The live list from the account's own /v1/models, with Claude Code's auth."""
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        token = "" if api_key else await asyncio.to_thread(claude_oauth_token)
        if not api_key and not token:
            return []
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        else:
            headers.update({"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
        async with httpx.AsyncClient() as h:
            r = await h.get("https://api.anthropic.com/v1/models", params={"limit": 1000}, headers=headers, timeout=15)
            r.raise_for_status()
        return [(m["id"], str(m.get("display_name") or m["id"]).removeprefix("Claude ")) for m in r.json()["data"]]

    def default_model(self, ids: list[str]) -> str:
        """The newest Opus the account offers (the list is newest first)."""
        return next((m for m in ids if "opus" in m), "")

    def owns(self, model: str) -> bool:
        return model.startswith("claude") or model in ("opus", "sonnet", "haiku")

    def login(self) -> Login:
        # `claude setup-token`: approve in the browser, paste the code it shows, get a long-lived token.
        argv = [self.cli(), "setup-token"]
        return Login(
            argv=argv, device=argv, paste=True,
            url=re.compile(r"https://(?:claude\.com|claude\.ai|platform\.claude\.com)/\S*oauth\S*"),
            token=re.compile(r"sk-ant-oat[A-Za-z0-9_-]+"), env_key="CLAUDE_CODE_OAUTH_TOKEN",
        )


# ---------------------------------------------------------------------- Codex


def _root(item):
    """Codex's protocol wraps each union member in a RootModel."""
    return getattr(item, "root", item)


def describe_codex_item(item) -> str:
    """The same one-liners Claude's tools get, for Codex's items."""
    kind = getattr(item, "type", "")
    if kind == "commandExecution":
        # Codex runs everything through a login shell: `/bin/zsh -lc 'npm test'`.
        m = re.fullmatch(r"\S*sh -l?c '(.*)'", item.command.strip(), re.S)
        return describe_tool("Bash", {"command": m.group(1).replace("'\\''", "'") if m else item.command})
    if kind == "mcpToolCall":
        return describe_tool(item.tool, item.arguments if isinstance(item.arguments, dict) else {})
    if kind == "fileChange" and item.changes:
        more = f" +{len(item.changes) - 1}" if len(item.changes) > 1 else ""
        return describe_tool("Edit", {"file_path": item.changes[0].path}) + more
    if kind == "webSearch" and item.query:
        return describe_tool("Search", {"query": item.query})
    return ""


class CodexSession(Session):
    """A persistent Codex thread over `codex app-server`: one process per session."""

    def __init__(self, brain: "CodexBrain", cwd: str, system, resume: str | None, model_fn=None) -> None:
        self.brain = brain
        self.model_fn = model_fn or (lambda: "")
        self.cwd = cwd
        self.system = system
        self.session_id = resume
        self.codex = None
        self.thread = None
        self.turn = None
        self.model = ""
        self.busy = False
        self._connecting = asyncio.Lock()

    async def _connect(self, resume: str | None) -> None:
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

        system = self.system() if callable(self.system) else self.system
        model = self.model_fn()
        codex = AsyncCodex(self.brain.sdk_config(self.cwd))
        # Full access and never asking: what Claude's bypassPermissions is to Claude Code.
        options = dict(
            cwd=self.cwd, sandbox=Sandbox.full_access, approval_mode=ApprovalMode.deny_all,
            developer_instructions=system, model=model or None, config=self.brain.thread_config(),
        )
        try:
            self.thread = await (codex.thread_resume(resume, **options) if resume else codex.thread_start(**options))
        except BaseException:
            await codex.close()
            raise
        self.codex = codex
        self.session_id = self.thread.id
        self.model = model

    async def _ensure(self) -> None:
        async with self._connecting:
            if self.codex is not None:
                return
            try:
                await self._connect(self.session_id)
            except Exception as e:
                if not self.session_id:
                    raise
                log.warning("codex resume failed (%s) — starting fresh", e)
                self.session_id = None
                await self._connect(None)

    async def warm(self) -> None:
        await self._ensure()

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        await self._ensure()
        model = self.model_fn()
        switch = model if model and model != self.model else None  # a turn's model sticks for the next ones
        self.busy = True
        last = final = None
        partial, writing = "", None
        try:
            self.turn = await self.thread.turn(prompt, model=switch)
            self.model = model or self.model
            async for ev in self.turn.stream():
                p = ev.payload
                if ev.method == "item/agentMessage/delta":
                    if p.item_id != writing:
                        writing, partial = p.item_id, ""
                    partial += p.delta
                    if on_delta is not None:
                        await on_delta(partial)
                elif ev.method == "item/started" and on_tool is not None:
                    line = describe_codex_item(_root(p.item))
                    if line:
                        await on_tool(line)
                elif ev.method == "item/completed":
                    item = _root(p.item)
                    text = (item.text or "").strip() if getattr(item, "type", "") == "agentMessage" else ""
                    if text and text != last:
                        last = final = text
                        await on_text(text)
                elif ev.method == "error" and not p.will_retry:
                    log.warning("codex error: %s", p.error.message)
                elif ev.method == "turn/completed" and p.turn.status.value == "failed":
                    raise RuntimeError(p.turn.error.message if p.turn.error else "Codex couldn't finish that.")
        finally:
            self.turn = None
            self.busy = False
        return final

    async def interrupt(self) -> None:
        turn = self.turn
        if turn is not None:
            await turn.interrupt()

    async def close(self) -> None:
        # Like Claude's: finish a connection that is still being made, then end its process.
        async with self._connecting:
            codex, self.codex, self.thread = self.codex, None, None
            self.busy = False
            if codex is not None:
                try:
                    await codex.close()
                except Exception:
                    pass


class CodexBrain(Brain):
    """OpenAI's Codex, signed in with ChatGPT (or an OpenAI API key)."""

    name = "codex"
    label = "ChatGPT"
    PREFIXES = ("gpt-", "codex", "o1", "o3", "o4")

    def __init__(self, *, mcp_factory=None) -> None:
        self.mcp_factory = mcp_factory
        self._signed_in, self._checked = False, float("-inf")  # `codex login status`, cached

    def session(self, *, cwd, system, resume=None, model_fn=None, recap=None) -> Session:
        return CodexSession(self, cwd, system, resume, model_fn)

    @staticmethod
    def bundled() -> str:
        """The Codex CLI that ships with the openai-codex SDK, or ''."""
        try:
            from codex_cli_bin import bundled_codex_path

            return str(bundled_codex_path())
        except (ImportError, FileNotFoundError):
            return ""

    def cli(self) -> str:
        return self.bundled() or shutil.which("codex") or "codex"

    def sdk_config(self, cwd: str | None = None):
        from openai_codex import CodexConfig

        # The SDK finds its bundled CLI (and the tools that ship beside it) itself.
        return CodexConfig(codex_bin=None if self.bundled() else self.cli(), cwd=cwd)

    def thread_config(self) -> dict:
        config: dict = {"web_search": "live"}  # Claude Code searches the live web too
        servers = self.mcp_factory() if self.mcp_factory else {}
        # Codex starts MCP servers as processes; an in-process (SDK) server is Claude's alone.
        processes = {name: spec for name, spec in servers.items() if spec.get("type", "stdio") == "stdio"}
        if processes:
            config["mcp_servers"] = {
                name: {k: v for k, v in spec.items() if k != "type"} for name, spec in processes.items()
            }
        return config

    def connected(self) -> bool:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        if (home / "auth.json").exists():
            return True
        # Codex can keep its sign-in in the OS keychain instead (cli_auth_credentials_store),
        # so ask the CLI itself. It's polled often; check at most every half minute.
        if time.monotonic() - self._checked > 30:
            self._checked = time.monotonic()
            try:
                status = subprocess.run([self.cli(), "login", "status"], capture_output=True, timeout=10)
                self._signed_in = status.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                self._signed_in = False
        return self._signed_in

    def forget(self) -> None:
        self._checked = float("-inf")

    async def models(self) -> list[tuple[str, str]]:
        from openai_codex import AsyncCodex

        async with AsyncCodex(self.sdk_config()) as codex:
            listed = [m for m in (await codex.models()).data if not m.hidden]
        return [(m.id, m.display_name) for m in sorted(listed, key=lambda m: not m.is_default)]

    def default_model(self, ids: list[str]) -> str:
        return ids[0] if ids else ""  # Codex's own default leads the list

    def owns(self, model: str) -> bool:
        return model.startswith(self.PREFIXES)

    def login(self) -> Login:
        cli = self.cli()
        return Login(
            argv=[cli, "login"],  # opens the browser and finishes there: nothing to paste
            device=[cli, "login", "--device-auth"],
            url=re.compile(r"https://auth\.openai\.com/\S+"),
            code=re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b"),
        )


# --------------------------------------------------------------------- routing

LEGACY = "claude"  # session ids saved before Ken had a second brain carry no prefix


class RoutedSession(Session):
    """A chat on whichever brain owns its model right now. One brain can't resume
    another's session, so moving brains opens a fresh one that gets the chat's
    recent turns instead."""

    def __init__(self, brains: "Brains", cwd, system, resume, model_fn, recap) -> None:
        self.brains = brains
        self.cwd = cwd
        self.system = system
        self.model_fn = model_fn
        self.recap = recap
        self.home, self.resume = brains.split(resume)
        self.inner: Session | None = None
        self._routing = asyncio.Lock()
        self._asking = False

    @property
    def session_id(self) -> str | None:
        if self.inner is not None and self.inner.session_id:
            return self.brains.join(self.home, self.inner.session_id)
        return self.brains.join(self.home, self.resume) if self.resume else None

    @property
    def busy(self) -> bool:
        return self._asking or bool(self.inner and self.inner.busy)

    async def _route(self, switch: bool = True) -> Session:
        """The session for the current model. switch=False keeps the one there is
        (a warm-up mid-reply must not end that reply: a new model is for the next task)."""
        async with self._routing:
            brain = self.brains.brain_for(self.model_fn())
            if self.inner is not None and (self.home == brain.name or not switch):
                return self.inner
            moving = self.inner is not None or bool(self.resume and self.home != brain.name)
            if self.inner is not None:
                await self.inner.close()
            resume = self.resume if self.home == brain.name else None
            system = self.system
            recent = self.recap() if moving and self.recap else ""
            if recent:
                base = self.system
                system = lambda: (base() if callable(base) else base) + "\n\n" + recent
            self.inner = brain.session(cwd=self.cwd, system=system, resume=resume, model_fn=self.model_fn)
            self.home, self.resume = brain.name, resume
            return self.inner

    async def warm(self) -> None:
        await (await self._route(switch=not self._asking)).warm()

    async def ask(self, prompt, on_text, on_tool=None, on_delta=None):
        self._asking = True
        try:
            return await (await self._route()).ask(prompt, on_text, on_tool, on_delta)
        finally:
            self._asking = False

    async def interrupt(self) -> None:
        if self.inner is not None:
            await self.inner.interrupt()

    async def close(self) -> None:
        inner, self.inner = self.inner, None
        if inner is not None:
            self.resume = inner.session_id or self.resume
            await inner.close()


class Brains(Brain):
    """Every brain Ken can think with. The first one is the default."""

    name = "brains"

    def __init__(self, brains: list[Brain], model_fn: Callable[[], str] = lambda: "") -> None:
        self.all = {b.name: b for b in brains}
        self.model_fn = model_fn
        self.catalog: dict[str, list[tuple[str, str]]] = {}  # brain -> its models, from refresh()

    def session(self, *, cwd, system, resume=None, model_fn=None, recap=None) -> Session:
        return RoutedSession(self, cwd, system, resume, model_fn or self.model_fn, recap)

    def live(self) -> list[Brain]:
        return [b for b in self.all.values() if b.connected()]

    def connected(self) -> bool:
        return bool(self.live())

    def brain_for(self, model: str) -> Brain:
        if model:
            for name, models in self.catalog.items():
                if name in self.all and any(m == model for m, _ in models):
                    return self.all[name]
            for b in self.all.values():
                if b.owns(model):
                    return b
        return (self.live() or list(self.all.values()))[0]

    def default_model(self, ids: list[str] | None = None) -> str:
        live = self.live()
        if not live:
            return ""
        first = live[0]
        return first.default_model([m for m, _ in self.catalog.get(first.name, [])])

    def choices(self) -> list[str]:
        return [m for b in self.live() for m, _ in self.catalog.get(b.name, [])]

    async def refresh(self) -> None:
        for b in self.live():
            try:
                self.catalog[b.name] = await b.models()
            except Exception as e:
                log.warning("%s model list refresh failed: %s", b.name, e)

    def split(self, sid: str | None) -> tuple[str, str | None]:
        name, sep, rest = (sid or "").partition(":")
        if sep and name in self.all:
            return name, rest
        return LEGACY, sid

    def join(self, name: str, sid: str) -> str:
        return sid if name == LEGACY else f"{name}:{sid}"
