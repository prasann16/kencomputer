"""Videos for Telegram: probe them, and re-encode when Telegram wouldn't play them inline
(anything but H.264/AAC in an mp4) or when they're over the bot upload limit."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("ken")


# HDR. Phones (iPhones by default) film in HDR: HLG or Dolby Vision, bt2020 colours, 10-bit. Squashed to a
# normal 8-bit video as if it were ordinary footage, it comes out pale and washed out, so every video Ken takes
# in is checked here and an HDR one is tone-mapped to normal (bt709) colours on the way in. Normal videos are
# left exactly as they are.
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG, PQ
# ffprobe's names for the source's colours, in zscale's words. Passed explicitly rather than trusting the tags to
# survive every filter on the way.
_ZSCALE_TRANSFER = {"arib-std-b67": "arib-std-b67", "smpte2084": "smpte2084", "bt2020-10": "2020_10", "bt2020-12": "2020_12"}
_ZSCALE_MATRIX = {"bt2020nc": "2020_ncl", "bt2020c": "2020_cl", "bt709": "709"}
# What an HDR video is tagged as after converting, so players don't guess.
BT709 = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def is_hdr(stream: dict) -> bool:
    """An ffprobe video stream that's HDR or wide-colour: HLG/PQ, bt2020 colours, or Dolby Vision."""
    return (stream.get("color_transfer") in HDR_TRANSFERS or stream.get("color_primaries") == "bt2020"
            or any("DOVI" in (d.get("side_data_type") or "") for d in stream.get("side_data_list") or []))


def video_stream(path: Path) -> dict:
    """The first video stream as ffprobe sees it (colours included), or {} if it can't be read."""
    if not shutil.which("ffprobe"):
        return {}
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-print_format", "json",
                        str(path)], capture_output=True, text=True)
    return next(iter(json.loads(r.stdout or "{}").get("streams") or []), {}) if not r.returncode else {}


@functools.lru_cache(maxsize=1)
def has_zscale() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(line.split()[1:2] == ["zscale"] for line in r.stdout.splitlines())


def sdr_filter(stream: dict) -> str:
    """ffmpeg filters that turn this HDR stream into normal 8-bit bt709 video (yuv420p) with its colours kept.
    The proper way is zscale's tone map; mobius keeps everyday colours as they are and only rolls off the
    highlights (hable, the usual pick, left faces and colours dull). An ffmpeg without zscale gets a plain
    colour-space conversion with a lift in saturation and contrast: close, not exact."""
    if has_zscale():
        tin = _ZSCALE_TRANSFER.get(stream.get("color_transfer"), "arib-std-b67")  # untagged Dolby Vision: iPhones' is HLG
        min_ = _ZSCALE_MATRIX.get(stream.get("color_space"), "2020_ncl")
        rin = "full" if stream.get("color_range") == "pc" else "limited"
        return (f"zscale=tin={tin}:pin=2020:min={min_}:rin={rin}:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=tonemap=mobius:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    return "colorspace=all=bt709:iall=bt2020:format=yuv420p,eq=saturation=1.3:contrast=1.3"


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
        "hdr": is_hdr(v),
        "stream": v,
    }


def needs_encode(path: Path, info: dict, limit: int) -> bool:
    return (
        path.suffix.lower() not in {".mp4", ".m4v"}
        or info["vcodec"] != "h264"
        or info["pix_fmt"] != "yuv420p"
        or info["acodec"] not in {"", "aac"}
        or info.get("hdr", False)
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
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2"  # H.264 needs even dimensions
    code, _ = await _run(
        "ffmpeg", "-y", "-v", "error", "-i", str(path),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", *rate,
        "-vf", f"{even},{sdr_filter(info['stream'])}" if info.get("hdr") else even,
        *(BT709 if info.get("hdr") else []),
        "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-movflags", "+faststart", str(out),
    )
    encoded = None if code else await probe(out)
    if encoded is None or out.stat().st_size > limit:
        out.unlink(missing_ok=True)
        return None
    return out, {"width": encoded["width"], "height": encoded["height"], "duration": round(encoded["duration"])}
