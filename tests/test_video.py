"""Videos reach the phone as playable videos: re-encoded when Telegram couldn't stream them, shrunk to fit."""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import video  # noqa: E402
from brains import skills_plugin  # noqa: E402
from engine import ensure_skills_plugin  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


def make_clip(path: Path, seconds: int = 2, codec: list[str] | None = None) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=321x241:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=duration={seconds}", *(codec or []), str(path)],
        check=True,
    )
    return path


def test_bitrate_fits_the_limit_or_gives_up():
    assert video.target_kbps(60, 50 * 1024 * 1024, True) > 5000
    assert video.target_kbps(3 * 3600, 50 * 1024 * 1024, True) is None  # 3 hours won't fit watchably
    assert video.target_kbps(0, 50 * 1024 * 1024, True) is None


@needs_ffmpeg
async def test_a_webm_becomes_an_h264_mp4_with_its_size(tmp_path):
    src = make_clip(tmp_path / "demo.webm")
    out, meta = await video.for_telegram(src, 50 * 1024 * 1024)
    assert out != src and out.suffix == ".mp4"
    info = await video.probe(out)
    assert info["vcodec"] == "h264" and info["pix_fmt"] == "yuv420p" and info["acodec"] == "aac"
    assert (info["width"], info["height"]) == (320, 240)  # odd sizes rounded down for H.264
    assert meta == {"width": 320, "height": 240, "duration": 2}  # what Telegram is actually sent
    out.unlink()


@needs_ffmpeg
async def test_a_ready_mp4_is_sent_as_is(tmp_path):
    src = make_clip(tmp_path / "ready.mp4", codec=["-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "scale=320:240", "-c:a", "aac"])
    out, meta = await video.for_telegram(src, 50 * 1024 * 1024)
    assert out == src and meta["width"] == 320


@needs_ffmpeg
async def test_an_oversized_video_is_shrunk_under_the_limit(tmp_path):
    src = make_clip(tmp_path / "big.mp4", seconds=4, codec=["-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", "-vf", "scale=320:240", "-c:a", "aac"])
    limit = 200 * 1024
    assert src.stat().st_size > limit
    out, _ = await video.for_telegram(src, limit)
    assert out != src and out.stat().st_size <= limit
    out.unlink()


async def test_not_a_video_goes_as_a_file(tmp_path):
    junk = tmp_path / "broken.mp4"
    junk.write_bytes(b"not a video")
    assert await video.for_telegram(junk, 50 * 1024 * 1024) is None


def test_workspace_skills_load_as_a_plugin_and_nothing_else_does(tmp_path):
    assert skills_plugin(str(tmp_path)) == []  # a repo's own .claude is never picked up on its own
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    assert skills_plugin(str(tmp_path)) == []
    ensure_skills_plugin(tmp_path)
    assert skills_plugin(str(tmp_path)) == [{"type": "local", "path": str(tmp_path / ".claude")}]
