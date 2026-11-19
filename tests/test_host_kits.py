"""Kits the host switches on per person (video kit, live data): switched off, Ken doesn't have them at all."""
import json

import engine


def kit(home, name):
    d = home / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n")


def linked(home):
    return sorted(p.name for p in engine.gather_skills(home, {}).iterdir())


def test_a_kit_is_in_kens_skills_only_when_the_host_says_its_on(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    for name in ("video-kit", "live-data", "video"):
        kit(tmp_path, name)
    assert linked(tmp_path) == ["video"]  # nothing said yet: off
    assert engine.save_kits(tmp_path, {"video-kit": True, "live-data": False}) is True
    assert linked(tmp_path) == ["video", "video-kit"]
    assert engine.save_kits(tmp_path, {"video-kit": True, "live-data": False}) is False  # no change
    engine.save_kits(tmp_path, {"video-kit": False, "live-data": True})
    assert linked(tmp_path) == ["live-data", "video"]  # switched off: gone
    assert engine.save_kits(tmp_path, None) is False  # an older host says nothing: unchanged
    assert json.loads((tmp_path / "kits-on.json").read_text()) == {"video-kit": False, "live-data": True}


def test_their_own_elevenlabs_key_keeps_the_video_kit(tmp_path, monkeypatch):
    kit(tmp_path, "video-kit")
    kit(tmp_path, "live-data")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_theirs")
    assert linked(tmp_path) == ["video-kit"]
