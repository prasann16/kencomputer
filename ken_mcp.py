"""Serve Ken's existing tool handlers over standard MCP for external runtimes."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


class SharedServer:
    """One stdio MCP server that every chat and job shares, served over HTTP by the app server.

    It's for the browser: Chrome asks the user to allow each new connection, so one server that
    stays connected means one "Allow" per Chrome restart instead of one per chat."""

    def __init__(self, command: str, args: list, env: dict | None = None):
        self.argv, self.env = [command, *args], env or {}
        self.proc = self.info = None
        self.pending: dict[int, asyncio.Future] = {}
        self.ids = 0
        self.starting = asyncio.Lock()

    def _write(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg).encode() + b"\n")

    def _send(self, method: str, params: dict) -> asyncio.Future:
        self.ids += 1
        self.pending[self.ids] = asyncio.get_running_loop().create_future()
        self._write({"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params})
        return self.pending[self.ids]

    async def _read(self, proc) -> None:
        while line := await proc.stdout.readline():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "method" in msg:  # the server asking us something (roots, sampling): we don't offer any
                if "id" in msg:
                    self._write({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Not supported"}})
            elif (fut := self.pending.pop(msg.get("id"), None)) and not fut.done():
                fut.set_result(msg)
        await proc.wait()
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result({"error": {"code": -32603, "message": "The browser tool stopped. Try again."}})
        self.pending.clear()

    async def ready(self) -> dict:
        """Start the server if it isn't running (first use, or it stopped) and return its hello."""
        async with self.starting:
            if self.proc is None or self.proc.returncode is not None:
                self.proc = await asyncio.create_subprocess_exec(
                    *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, env={**os.environ, **self.env}, limit=64 * 1024 * 1024)
                asyncio.create_task(self._read(self.proc))
                hello = await asyncio.wait_for(self._send("initialize", {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ken", "version": "1"}}), 60)
                if "error" in hello:
                    raise RuntimeError(hello["error"].get("message", "The browser tool didn't start."))
                self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
                self.info = hello["result"]
            return self.info

    async def handle(self, msg: dict) -> dict | None:
        """Answer one JSON-RPC message from a chat's MCP client; None for a notification."""
        if "id" not in msg:
            return None
        method, params = msg.get("method"), msg.get("params") or {}
        try:
            if method == "initialize":
                reply = {"result": {**await self.ready(), "protocolVersion": params.get("protocolVersion", "2025-06-18")}}
            elif method == "ping":
                reply = {"result": {}}
            elif method in ("tools/list", "tools/call"):
                await self.ready()
                reply = await self._send(method, params)
            else:
                reply = {"error": {"code": -32601, "message": f"Unknown method {method}"}}
        except Exception as exc:
            reply = {"error": {"code": -32603, "message": str(exc) or "The browser tool didn't start."}}
        return {"jsonrpc": "2.0", "id": msg["id"], **{k: reply[k] for k in ("result", "error") if k in reply}}


def external_server(kind: str, home: Path, only=None) -> dict:
    args = [] if getattr(sys, "frozen", False) else [str(Path(__file__).with_name("bot.py"))]
    args += ["--ken-mcp", kind, str(home), *sorted(only or [])]
    return {"type": "stdio", "command": sys.executable, "args": args}


def media_server():
    from claude_agent_sdk import create_sdk_mcp_server, tool
    from voice import captions

    async def run(args):
        try:
            return {"content": [{"type": "text", "text": await asyncio.to_thread(captions, str(args["path"]))}]}
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"Failed: {exc}"}], "is_error": True}

    return create_sdk_mcp_server("media", tools=[tool(
        "transcribe_media", "Transcribe a video or audio file on this computer: returns the transcript with "
        "timestamps and saves subtitles (.srt) next to the file. Takes a few minutes per hour of audio.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})(run)])


async def serve(kind, home, only):
    from mcp.server.stdio import stdio_server
    from connections import Connections

    config = media_server() if kind == "media" else Connections(Path(home)).mcp(set(only)).get("ken")
    if config is None:
        raise RuntimeError("No connected tools are enabled")
    server = config["instance"]
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    kind, home, *only = sys.argv[sys.argv.index("--ken-mcp") + 1:]
    if kind not in ("media", "connections"):
        raise ValueError("Unknown Ken MCP server")
    asyncio.run(serve(kind, home, only))
