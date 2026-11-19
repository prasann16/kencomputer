import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "skills" / "viral-content"
spec = importlib.util.spec_from_file_location("viral", SKILL / "viral.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def test_the_skill_says_what_it_is_for_so_the_ai_picks_it():
    text = (SKILL / "SKILL.md").read_text()
    head = text.split("---")[1]
    assert "name: viral-content" in head and "description:" in head
    assert "Never post" in text  # show first: the creator says yes to every post


def test_every_link_gets_a_folder_named_after_its_video():
    assert v.slug("https://www.youtube.com/shorts/-98SIPi79_I") == "-98SIPi79_I"
    assert v.slug("https://www.instagram.com/reel/DAbc123xyz/?igsh=1") == "DAbc123xyz"
    assert v.slug("https://www.tiktok.com/@kara/video/7312345678901234567") == "7312345678901234567"
    assert v.platform("https://youtu.be/abc") == "youtube" and v.platform("https://www.instagram.com/reel/x") == "instagram"


def test_an_outlier_needs_enough_videos_to_compare_against():
    assert v.median([100, 200, 300]) is None  # 3 videos say nothing about an account's usual
    assert v.median([0, 100, 200, 300, 400, 500]) == 300  # a hidden count (0) isn't a zero-view video


def reel(root, rid, outlier, views, fmt, hook):
    d = root / "reels" / rid
    (d / "hook-frames").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"title": rid}))
    (d / "signals.json").write_text(json.dumps({
        "id": rid, "url": f"https://www.youtube.com/shorts/{rid}", "account": "acct",
        "measured": {"duration_s": 30.0, "cuts_per_10s": 2.0, "outlier": outlier, "views": views},
        "judged": {**v.JUDGED, "format": fmt, "hook_type": hook, "hook_text": f"hook {rid}"}}))


def test_the_board_puts_the_biggest_outlier_first_not_the_biggest_account(tmp_path, monkeypatch):
    reel(tmp_path, "big", 2.0, 9_000_000, "talking head", "text")
    reel(tmp_path, "breakout", 40.0, 80_000, "reveal tour", "voice + text")
    monkeypatch.setattr(v, "shot", lambda *a: None)
    v.cmd_board(type("A", (), {"dir": str(tmp_path), "niche": "travel"}))
    md = (tmp_path / "board.md").read_text()
    assert md.index("hook breakout") < md.index("hook big")
    assert "1 of 2 hook with their voice" in md and "tour (1)" in md
    assert (tmp_path / "board.html").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")
def test_a_reel_is_measured_hook_frames_cuts_and_length(tmp_path):
    d = tmp_path / "reels" / "x"
    d.mkdir(parents=True)
    # 4 s vertical video: red for 2 s, then a hard cut to blue
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=180x320:d=2", "-f", "lavfi",
                    "-i", "color=blue:s=180x320:d=2", "-filter_complex", "[0][1]concat=n=2:v=1", str(d / "video.mp4")],
                   check=True)
    s = v.analyze_one(d, whisper=False)
    m = s["measured"]
    assert m["vertical"] and 3.5 < m["duration_s"] < 4.5
    assert m["cuts"] == 1 and 1.8 < m["first_cut_s"] < 2.2 and m["cuts_in_first_3s"] == 1
    assert (d / "hook.jpg").exists() and (d / "sheet.jpg").exists() and len(list((d / "hook-frames").glob("*.jpg"))) == 6
    assert s["judged"]["format"] is None  # the AI fills this in by looking
