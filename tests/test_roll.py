import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import roll  # noqa: E402


def test_days_day_and_send(monkeypatch, capsys):
    asked = []
    wants = []

    def fake(path, body=None, timeout=30):
        asked.append((path, body))
        if path.startswith("days?"):
            return {"total": 2503, "days": [{"day": "2026-07-12", "photos": 4, "videos": 2, "seconds": 43}]}
        if path.startswith("day?"):
            return {"items": [{"id": "v1", "t": 1783846800000, "k": "video", "d": 31, "w": 1080, "h": 1920}]}
        if path == "want":
            wants.extend(body["ids"])
            return {"asked": len(body["ids"])}
        if path == "wants":
            return {"wants": [{"id": "v1", "sent_at": 1, "library_id": "abc"}, {"id": "v2", "sent_at": None}]}
        return {}

    monkeypatch.setattr(roll, "call", fake)
    assert roll.main(["days", "--kind", "video", "--from", "2026-06-01"]) == 0
    out = capsys.readouterr().out
    assert "2026-07-12 (Sun) · 2 videos (43s), 4 photos" in out and "of 2503" in out
    assert "kind=video" in asked[0][0] and "from=" in asked[0][0] and "tz=" in asked[0][0]
    assert roll.main(["day", "2026-07-12", "--kind", "video"]) == 0
    assert "v1 · " in capsys.readouterr().out
    assert roll.main(["send", "v1", "v2", "--wait", "0"]) == 0
    out = capsys.readouterr().out
    assert wants == ["v1", "v2"] and "v1: in their Library as abc" in out and "v2: waiting for their phone" in out and "open it" in out


def test_off_says_so(monkeypatch, capsys):
    def off(*a, **k):
        raise roll.Off()
    monkeypatch.setattr(roll, "call", off)
    assert roll.main(["days"]) == 1
    assert "isn't switched on" in capsys.readouterr().err
