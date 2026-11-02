"""Videos reach the phone as playable videos: re-encoded when Telegram couldn't stream them, shrunk to fit."""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import video  # noqa: E402
from engine import Engine, prepare_skills  # noqa: E402

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


def test_skills_live_in_one_folder_that_claude_loads_as_a_plugin(tmp_path):
    plugin = prepare_skills(tmp_path)
    assert plugin == tmp_path / ".skills-plugin"
    assert (plugin / ".claude-plugin" / "plugin.json").is_file()
    assert (plugin / "skills").resolve() == (tmp_path / ".skills-in-use").resolve()
    (tmp_path / "skills" / "fruit").mkdir()
    (tmp_path / "skills" / "fruit" / "SKILL.md").write_text("---\nname: fruit\n---\n")
    assert prepare_skills(tmp_path) == plugin  # every start: nothing to redo
    assert (plugin / "skills" / "fruit" / "SKILL.md").is_file()  # what Claude sees is Ken's skills folder


def test_an_older_ken_link_moves_to_the_skills_in_use(tmp_path):
    import os
    from engine import link_dir
    (tmp_path / "skills").mkdir()
    (tmp_path / ".skills-plugin").mkdir()
    link_dir(tmp_path / ".skills-plugin" / "skills", tmp_path / "skills")
    prepare_skills(tmp_path)
    assert os.path.realpath(tmp_path / ".skills-plugin" / "skills") == os.path.realpath(tmp_path / ".skills-in-use")
    assert (tmp_path / "skills").is_dir()  # the old target is left alone


def test_adding_a_skill_reopens_open_chats(tmp_path):
    import os
    from test_engine import FakeBrain
    e = Engine(tmp_path, FakeBrain())
    prepare_skills(tmp_path)
    before = e._tools_stamp("home")
    os.utime(tmp_path / "skills", (1, 1))
    assert e._tools_stamp("home") != before


def test_codex_finds_kens_skills_in_kens_own_folders_only(tmp_path):
    from test_engine import FakeBrain
    e = Engine(tmp_path, FakeBrain())
    prepare_skills(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    e._share_skills(work)
    assert (work / ".agents" / "skills").resolve() == (tmp_path / ".skills-in-use").resolve()
    theirs = tmp_path.parent / (tmp_path.name + "-their-code")
    theirs.mkdir()
    e._share_skills(theirs)
    assert not (theirs / ".agents").exists()  # a folder of theirs is never written to


def test_brain_lists_kens_skills_and_removes_one(tmp_path):
    import pytest
    from test_engine import FakeBrain
    e = Engine(tmp_path, FakeBrain())
    prepare_skills(tmp_path)
    (tmp_path / "skills" / "remotion").mkdir()
    (tmp_path / "skills" / "remotion" / "SKILL.md").write_text("---\nname: remotion-best-practices\ndescription: \"Make videos in React\"\n---\nbody\n")
    (tmp_path / "skills" / "not-a-skill").mkdir()  # no SKILL.md: not listed
    assert e.about("home")["skills"] == [{"id": "remotion", "name": "remotion-best-practices", "about": "Make videos in React"}]
    with pytest.raises(KeyError):
        e.remove_skill("../work")  # only a skill folder, never anything else
    (tmp_path / "skills" / "remotion" / "create").mkdir()
    page = e.read_skill("remotion")
    assert page["name"] == "remotion-best-practices" and page["text"] == "body" and page["parts"] == ["create"]
    assert e.remove_skill("remotion") == []
    assert not (tmp_path / "skills" / "remotion").exists()


def test_skills_from_other_ai_apps_are_used_and_can_be_switched_off(tmp_path):
    import os
    import pytest
    from test_engine import FakeBrain

    def skill(d, name, about):
        (d / name).mkdir(parents=True)
        (d / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {about}\n---\nDo {name}.\n")

    home, claude = tmp_path / "ken", tmp_path / "claude-skills"
    home.mkdir()
    skill(claude, "pdf", "Fill PDFs")
    skill(claude, "video", "Their own video skill")
    skill(home / "skills", "video", "Ken's video skill")
    (claude / ".hidden").mkdir()
    e = Engine(home, FakeBrain())
    e.other_skills = {"Claude Code": [claude], "Codex": [tmp_path / "nothing-here"]}
    plugin = prepare_skills(home, others=e.other_skills)
    seen = plugin / "skills"
    assert (seen / "pdf" / "SKILL.md").read_text().startswith("---\nname: pdf")  # their skill, read where it is
    assert "Ken's video" in (seen / "video" / "SKILL.md").read_text()  # a name Ken has stays Ken's
    skills = {s["id"]: s for s in e.list_skills()}
    assert skills["claude-code:pdf"] == {"id": "claude-code:pdf", "name": "pdf", "about": "Fill PDFs", "app": "Claude Code", "on": True}
    assert skills["claude-code:video"]["same_name"] and "app" not in skills["video"]
    assert e.read_skill("claude-code:pdf")["text"] == "Do pdf."

    before = e._tools_stamp("home")
    e.switch_skill("claude-code:pdf", False)
    assert not os.path.lexists(seen / "pdf") and (claude / "pdf" / "SKILL.md").is_file()  # off for Ken, theirs untouched
    assert e._tools_stamp("home") != before  # open chats reopen without it
    e.switch_skill("claude-code:pdf", True)
    assert (seen / "pdf" / "SKILL.md").is_file()

    skill(claude, "notes", "Take notes")  # made in Claude Code while Ken runs
    e.sync_skills()
    assert (seen / "notes" / "SKILL.md").is_file()
    with pytest.raises(KeyError):
        e.remove_skill("claude-code:pdf")  # Ken never deletes another app's skill
    with pytest.raises(KeyError):
        e.switch_skill("video", False)
