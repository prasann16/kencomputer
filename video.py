"""Videos for Telegram: probe them, and re-encode when Telegram wouldn't play them inline
(anything but H.264/AAC in an mp4) or when they're over the bot upload limit."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

log = logging.getLogger("ken")

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_KBPS = 96
MIN_VIDEO_KBPS = 150  # below this a shrunk video isn't worth watching; send it as a file or not at all


async def _run(*cmd: str) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode:
        log.warning("%s failed: %s", cmd[0], err.decode(errors="replace")[-400:])
    return proc.returncode, out


async def probe(path: Path) -> dict | None:
    """Width, height, duration (s), codecs and pixel format, or None if ffprobe can't read it."""
    if not shutil.which("ffprobe"):
        return None
    code, out = await _run("ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path))
    if code:
        return None
    data = json.loads(out or b"{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        return None
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "duration": float(data.get("format", {}).get("duration") or v.get("duration") or 0),
        "vcodec": v.get("codec_name", ""),
        "pix_fmt": v.get("pix_fmt", ""),
        "acodec": a.get("codec_name", "") if a else "",
    }


def needs_encode(path: Path, info: dict, limit: int) -> bool:
    return (
        path.suffix.lower() not in {".mp4", ".m4v"}
        or info["vcodec"] != "h264"
        or info["pix_fmt"] != "yuv420p"
        or info["acodec"] not in {"", "aac"}
        or path.stat().st_size > limit
    )


def target_kbps(duration: float, limit: int, has_audio: bool) -> int | None:
    """Video bitrate that lands the file under `limit` with headroom for the container, or
    None when the video is too long to fit at a watchable quality."""
    if duration <= 0:
        return None
    total = limit * 8 * 0.9 / duration / 1000
    video = int(total - (AUDIO_KBPS if has_audio else 0))
    return video if video >= MIN_VIDEO_KBPS else None


async def for_telegram(path: Path, limit: int) -> tuple[Path, dict] | None:
    """A path Telegram can stream inline, plus its width/height/duration. The path is a new
    temp file when it had to be re-encoded (the caller deletes it). None: send it as a file."""
    info = await probe(path)
    if info is None or not shutil.which("ffmpeg"):
        return None
    meta = {"width": info["width"], "height": info["height"], "duration": round(info["duration"])}
    if not needs_encode(path, info, limit):
        return path, meta
    rate = ["-crf", "23"]
    if path.stat().st_size > limit:
        kbps = target_kbps(info["duration"], limit, bool(info["acodec"]))
        if kbps is None:
            return None
        rate = ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"]
    fd, name = tempfile.mkstemp(suffix=".mp4", prefix="ken-video-")
    os.close(fd)
    out = Path(name)
    code, _ = await _run(
        "ffmpeg", "-y", "-v", "error", "-i", str(path),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", *rate,
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # H.264 needs even dimensions
        "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-movflags", "+faststart", str(out),
    )
    encoded = None if code else await probe(out)
    if encoded is None or out.stat().st_size > limit:
        out.unlink(missing_ok=True)
        return None
    return out, {"width": encoded["width"], "height": encoded["height"], "duration": round(encoded["duration"])}
