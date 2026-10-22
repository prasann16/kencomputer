"""Serve Ken's existing tool handlers over standard MCP for external runtimes."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


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
