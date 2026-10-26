import json

import pytest

import video_kit as v


def words(*spans):
    return [{"w": f"w{i}", "s": s, "e": e} for i, (s, e) in enumerate(spans)]


def loud_for(quiet, length):
    """Talking everywhere except the quiet stretches, every 20 ms."""
    return [not any(q0 <= i * v.HOP < q1 for q0, q1 in quiet) for i in range(int(length / v.HOP))]


def pieces(w, keep, quiet, length):
    return v._pieces(w, keep, quiet, loud_for(quiet, length), length)


def test_cut_ends_where_the_voice_stops_not_where_whisper_says():
    # Whisper stretched "w1" across the pause after it; the audio says the voice stopped at 1.3.
    w = words((0.5, 0.9), (0.9, 3.0), (3.1, 3.5))
    quiet = [(0.0, 0.45), (1.3, 3.05), (3.6, 4.0)]
    assert pieces(w, [(0, 1), (2, 2)], quiet, 4)[0] == [0.3, 1.6, True]  # a breath before, the fade after, nothing of w2


def test_the_last_word_of_the_reel_gets_a_moment_to_settle():
    w = words((0.5, 0.9), (0.9, 1.3))
    last = pieces(w, [(0, 1)], [(0.0, 0.45), (1.3, 4.0)], 4)[-1]
    assert last[1] == pytest.approx(1.3 + v.END_HOLD)


def test_a_long_pause_inside_is_shortened_not_removed():
    w = words((0.2, 1.0), (3.0, 3.6))
    p = pieces(w, [(0, 1)], [(1.0, 3.0), (3.6, 4.0)], 4)
    assert len(p) == 2 and p[0][2] and not p[1][2]  # the second part isn't a real cut, so no punch-in there
    assert (p[0][1] - 1.0) + (3.0 - p[1][0]) == pytest.approx(v.KEEP_PAUSE)


def test_a_cut_never_reaches_back_into_a_dropped_word():
    w = words((0.2, 0.6), (0.6, 0.9), (0.9, 1.5))  # "um" (w1) right before w2, no pause between
    assert pieces(w, [(0, 0), (2, 2)], [(1.5, 2.0)], 2)[-1][0] >= 0.9


def test_every_kept_word_lands_on_the_new_timeline():
    p = [[1.0, 2.0, True], [5.0, 6.0, True]]
    assert v._retime(1.5, p) == 0.5
    assert v._retime(5.5, p) == 1.5
    assert v._retime(2.2, p) == 1.0  # just after a part: its nearest edge
    assert v._retime(4.9, p) == 1.0  # just before the next one: its start


def test_hard_edges_are_reported():
    p = [[0.0, 0.5, True], [1.0, 1.5, True]]
    assert v._hard_edges(p, [True] * 100) == [0.5, 1.0]
    assert v._hard_edges(p, [False] * 100) == []


def test_framing_starts_normal_and_only_real_cuts_count():
    cuts, framing = v._framing([[0.0, 3.0, True], [10.0, 13.0, True], [13.7, 15.0, False], [20.0, 22.0, True]], 1.0)
    assert framing[0] == {"at": 0.0, "scale": 1.0}
    assert cuts == [3.0, 7.3]  # the shortened pause at 6.0 isn't a jump cut
    assert [f["at"] for f in framing] == [0.0, 3.0, 7.3]
    assert len({f["scale"] for f in framing[:2]}) == 2  # neighbours never share a framing


def test_keep_ranges_and_listing():
    assert v._parse_keep("0-2, 4", 6) == [(0, 2), (4, 4)]
    assert v._parse_keep("all", 6) == [(0, 5)]
    with pytest.raises(SystemExit):
        v._parse_keep("3-9", 6)
    with pytest.raises(SystemExit, match="keep their order"):
        v._parse_keep("4-5,0-2", 6)
    w = [{"w": "Hi.", "s": 0, "e": 0.3}, {"w": "So", "s": 1.5, "e": 1.7}, {"w": "yes", "s": 1.7, "e": 2}]
    assert v._listing(w) == "[0:00.0] 0:Hi.\n   (pause 1.2s)\n[0:01.5] 1:So 2:yes"


def test_render_falls_back_to_chrome_when_the_fast_way_fails(tmp_path, monkeypatch):
    (tmp_path / "props.json").write_text(json.dumps({"durationSeconds": 5}))
    calls = []
    monkeypatch.setattr(v, "_project", lambda *a, **k: None)
    monkeypatch.setattr(v, "_node", lambda home, folder, args: calls.append(args[0]) or (1 if args[0] == "compose.mjs" else 0))
    v.render(tmp_path, tmp_path, say=lambda *_: None)
    assert calls == ["compose.mjs", "render.mjs"]


def test_whip_pans_go_straight_to_chrome(tmp_path, monkeypatch):
    (tmp_path / "props.json").write_text(json.dumps({"durationSeconds": 5, "chapters": [2.0]}))
    calls = []
    monkeypatch.setattr(v, "_project", lambda *a, **k: None)
    monkeypatch.setattr(v, "_node", lambda home, folder, args: calls.append(args[0]) or 0)
    v.render(tmp_path, tmp_path, say=lambda *_: None)
    assert calls == ["render.mjs"]


def test_a_reel_from_the_older_tools_asks_to_be_cut_again(tmp_path):
    (tmp_path / "reel.json").write_text("{}")
    with pytest.raises(SystemExit, match="Cut it again"):
        v.render(tmp_path, tmp_path)


def test_a_machine_built_with_the_kit_skips_setup(tmp_path, monkeypatch):
    kit = tmp_path / "kit"
    (kit / "node_modules").mkdir(parents=True)
    monkeypatch.setenv("KEN_VIDEO_KIT", str(kit))
    assert v.kit_dir(tmp_path) == kit
    assert v.ready(tmp_path)
    assert v.setup(tmp_path, say=lambda *_: None) == kit  # no Node download, no npm
