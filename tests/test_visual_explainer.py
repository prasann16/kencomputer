import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "skills" / "visual-explainer"
spec = importlib.util.spec_from_file_location("visual_render", SKILL / "render.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def test_new_copies_a_template_and_never_overwrites(tmp_path):
    r.new(tmp_path / "a", False)
    r.new(tmp_path / "b", True)
    assert "data-step" in (tmp_path / "a" / "visual.html").read_text()
    assert "THREE" in (tmp_path / "b" / "visual.html").read_text()
    with pytest.raises(SystemExit):
        r.new(tmp_path / "a", False)


def test_steps_counts_parts_and_captions(tmp_path):
    html = tmp_path / "v.html"
    html.write_text('<g data-step="1"></g><g data-step="3"></g><p data-only="4"></p>')
    assert r.steps_in(html) == 4
    html.write_text("<svg></svg>")
    assert r.steps_in(html) == 0


def test_every_example_has_its_picture():
    for html in (SKILL / "examples").glob("*.html"):
        assert html.with_suffix(".png").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_renders_a_phone_png_and_steps_mp4(tmp_path):
    try:
        r.find_chrome()
    except SystemExit:
        pytest.skip("no Chrome here")
    r.new(tmp_path / "v", False)
    r.main([str(tmp_path / "v"), "--steps"])
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(tmp_path / "v" / "visual.png")], capture_output=True, text=True)
    assert size.stdout.strip() == "1080,1350"
    assert (tmp_path / "v" / "step-2.png").exists() and (tmp_path / "v" / "steps.mp4").stat().st_size > 0
