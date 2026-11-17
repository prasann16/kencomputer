"""HDR phone footage keeps its colours: it's tone-mapped to normal (bt709) video wherever Ken takes a video in,
and normal footage is left alone."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import video
import video_kit

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
# The HDR test clip itself is made with zscale, so without it there's nothing to test with.
needs_zscale = pytest.mark.skipif(not (shutil.which("ffmpeg") and video.has_zscale()), reason="ffmpeg has no zscale")

FRAME = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"  # how the cut frames it


def make_hdr(path: Path, transfer: str = "arib-std-b67") -> Path:
    """testsrc2 as an iPhone would film it: HLG (or PQ), bt2020, 10-bit, white at HDR's reference level (203 nits).
    The original SDR picture is the right answer to compare against."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2", "-f", "lavfi",
                    "-i", "sine=d=2", "-vf", "zscale=tin=bt709:t=linear:pin=bt709:p=bt2020:min=bt709:rin=tv,format=gbrpf32le,"
                    f"zscale=t={transfer}:npl=203:m=bt2020nc:r=tv,format=yuv422p10le", "-c:v", "prores_ks",
                    "-color_primaries", "bt2020", "-color_trc", transfer, "-colorspace", "bt2020nc", "-c:a", "aac",
                    str(path)], check=True)
    return path


def make_sdr(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2", "-f", "lavfi",
                    "-i", "sine=d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


def look(cmd_in: list[str], vf: str) -> tuple[float, float]:
    """Saturation (mean max-min of RGB) and contrast (spread of brightness) of one frame."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", *cmd_in, "-vf", f"{vf},scale=270:480", "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(float) / 255
    return float((px.max(1) - px.min(1)).mean()), float(px.mean(1).std())


def reference() -> tuple[float, float]:
    return look(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2"], FRAME)


def tags(path: Path) -> tuple:
    s = video.video_stream(path)
    return s.get("color_primaries"), s.get("color_transfer"), s.get("color_space")


def kept_colours(path: Path, vf: str = "null") -> bool:
    sat, contrast = look(["-i", str(path)], vf)
    ref_sat, ref_contrast = reference()
    return sat >= 0.65 * ref_sat and contrast >= 0.7 * ref_contrast  # the old squash: 0.5 and 0.66


def test_hdr_is_recognised():
    assert video.is_hdr({"color_transfer": "arib-std-b67", "color_primaries": "bt2020"})  # iPhone HLG
    assert video.is_hdr({"color_transfer": "smpte2084"})  # PQ
    assert video.is_hdr({"color_primaries": "bt2020", "color_transfer": "bt2020-10"})
    assert video.is_hdr({"side_data_list": [{"side_data_type": "DOVI configuration record"}]})  # Dolby Vision
    assert not video.is_hdr({"color_transfer": "bt709", "color_primaries": "bt709"})
    assert not video.is_hdr({})  # untagged: an ordinary video


@needs_zscale
def test_squashing_hdr_without_converting_is_what_washed_it_out(tmp_path):
    # The check below must catch the old bug, or it proves nothing.
    src = make_hdr(tmp_path / "hdr.mov")
    assert not kept_colours(src, "format=yuv420p," + FRAME)
    assert kept_colours(src, video.sdr_filter(video.video_stream(src)) + "," + FRAME)


@needs_zscale
def test_pq_footage_keeps_its_colours_too(tmp_path):
    src = make_hdr(tmp_path / "pq.mov", "smpte2084")
    assert kept_colours(src, video.sdr_filter(video.video_stream(src)) + "," + FRAME)


@needs_ffmpeg
@pytest.mark.parametrize("zscale", [True, False], ids=["zscale", "fallback"])
@pytest.mark.parametrize("tagged", ["color_trc=arib-std-b67", "color_trc=smpte2084", "color_primaries=bt2020",
                                    "color_primaries=bt2020:color_trc=bt709", "", "color_primaries=bt2020:color_trc="
                                    "arib-std-b67:colorspace=bt2020nc:range=pc"])
def test_hdr_tagged_only_in_part_still_converts(tmp_path, monkeypatch, zscale, tagged):
    # Phones and apps don't always tag everything; a clip that's HDR by any one sign must convert, never fail.
    if zscale and not video.has_zscale():
        pytest.skip("ffmpeg has no zscale")
    monkeypatch.setattr(video, "has_zscale", lambda: zscale)
    clip = tmp_path / "clip.mkv"  # mkv keeps a lone tag (mov only stores all three together)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=0.2", "-vf",
                    f"format=yuv422p10le,setparams={tagged}" if tagged else "format=yuv422p10le", "-c:v", "prores_ks",
                    str(clip)], check=True)
    stream = video.video_stream(clip)
    if not tagged:  # Dolby Vision known only from its side data
        stream["side_data_list"] = [{"side_data_type": "DOVI configuration record"}]
    assert video.is_hdr(stream)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-vf", video.sdr_filter(stream), "-f", "null", "-"], check=True)


@needs_zscale
def test_the_palette_of_hdr_footage_is_read_as_the_reel_will_look(tmp_path):
    folder = tmp_path / "reel"
    folder.mkdir()
    make_hdr(folder / "source.mov")
    assert video_kit.colors(folder, lambda _: None)["overall"]["saturation"] > 0.6


def cut_folder(tmp_path: Path, make) -> Path:
    folder = tmp_path / "reel"
    folder.mkdir()
    make(folder / "source.mov")
    (folder / "source.words.json").write_text(json.dumps([{"w": "Hello.", "s": 0.1, "e": 1.9}]))
    return folder


@needs_zscale
@pytest.mark.parametrize("zscale", [True, False], ids=["zscale", "fallback"])
def test_a_cut_of_hdr_footage_keeps_its_colours(tmp_path, monkeypatch, zscale):
    monkeypatch.setattr(video, "has_zscale", lambda: zscale)
    folder = cut_folder(tmp_path, make_hdr)
    said = []
    video_kit.cut(folder, "all", said.append)
    out = folder / "cut.mp4"
    assert tags(out) == ("bt709", "bt709", "bt709")
    assert video.video_stream(out)["pix_fmt"] == "yuv420p"
    assert kept_colours(out)
    assert any("HDR" in line for line in said)


@needs_ffmpeg
def test_normal_footage_is_cut_as_before(tmp_path, monkeypatch):
    def no(*_):
        raise AssertionError("a normal video must not be converted")

    monkeypatch.setattr(video, "sdr_filter", no)
    folder = cut_folder(tmp_path, make_sdr)
    video_kit.cut(folder, "all", lambda _: None)
    assert tags(folder / "cut.mp4")[1] in (None, "unknown")  # no colour tags added: the same command as always


@needs_zscale
def test_an_hdr_clip_in_a_graphic_is_converted_once(tmp_path):
    clip = make_hdr(tmp_path / "broll.mov")
    props = {"graphics": [{"type": "Broll", "props": {"src": "broll.mov"}}, {"type": "design", "props": {}}]}
    video_kit._sdr_clips(tmp_path, props, lambda _: None)
    assert tags(clip) == ("bt709", "bt709", "bt709") and kept_colours(clip, FRAME)
    stamp = clip.stat().st_mtime_ns
    video_kit._sdr_clips(tmp_path, props, lambda _: None)  # already normal: left alone
    assert clip.stat().st_mtime_ns == stamp


@needs_ffmpeg
def test_a_normal_clip_in_a_graphic_is_left_alone(tmp_path):
    clip = make_sdr(tmp_path / "broll.mp4")
    stamp = clip.stat().st_mtime_ns
    video_kit._sdr_clips(tmp_path, {"graphics": [{"props": {"src": "broll.mp4"}}]}, lambda _: None)
    assert clip.stat().st_mtime_ns == stamp


@needs_zscale
async def test_hdr_sent_to_telegram_keeps_its_colours(tmp_path):
    src = make_hdr(tmp_path / "hdr.mov")
    out, _ = await video.for_telegram(src, 50 * 1024 * 1024)
    assert out != src and tags(out) == ("bt709", "bt709", "bt709") and kept_colours(out, FRAME)
    out.unlink()
