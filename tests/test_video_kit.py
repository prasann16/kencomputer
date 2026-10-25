import pytest

import video_kit as v


def words(*spans):
    return [{"w": f"w{i}", "s": s, "e": e} for i, (s, e) in enumerate(spans)]


def test_cut_ends_where_the_voice_stops_not_where_whisper_says():
    # Whisper stretched "w1" across the pause after it; the audio says the voice stopped at 1.3.
    w = words((0.5, 0.9), (0.9, 3.0), (3.1, 3.5))
    pieces = v._pieces(w, [(0, 1)], quiet=[(0.0, 0.45), (1.3, 3.05)], length=4)
    assert pieces == [[0.3, 1.6]]  # a breath before, the fade after, nothing of w2


def test_a_long_pause_inside_is_shortened_not_removed():
    w = words((0.2, 1.0), (3.0, 3.6))
    pieces = v._pieces(w, [(0, 1)], quiet=[(1.0, 3.0), (3.6, 4.0)], length=4)
    assert pieces[0][1] - pieces[0][0] > 0.8 and pieces[1][0] - pieces[0][1] == pytest.approx(2.0 - v.KEEP_PAUSE)
    gap_kept = (pieces[0][1] - 1.0) + (3.0 - pieces[1][0])
    assert gap_kept == pytest.approx(v.KEEP_PAUSE)


def test_a_cut_never_reaches_back_into_a_dropped_word():
    w = words((0.2, 0.6), (0.6, 0.9), (0.9, 1.5))  # "um" (w1) right before w2, no pause between
    pieces = v._pieces(w, [(0, 0), (2, 2)], quiet=[(1.5, 2.0)], length=2)
    assert pieces[-1][0] >= 0.9


def test_every_kept_word_lands_on_the_new_timeline():
    pieces = [[1.0, 2.0], [5.0, 6.0]]
    assert v._retime(1.5, pieces) == 0.5
    assert v._retime(5.5, pieces) == 1.5
    assert v._retime(3.0, pieces) == 1.0  # inside a cut: snaps to where the next part starts


def test_hard_edges_are_reported():
    loud = [True] * 100  # talking the whole time
    assert v._hard_edges([[0.0, 0.5], [1.0, 1.5]], loud) == [0.5, 1.0]
    quiet = [False] * 100
    assert v._hard_edges([[0.0, 0.5], [1.0, 1.5]], quiet) == []


def test_keep_ranges_and_listing():
    assert v._parse_keep("0-2, 4", 6) == [(0, 2), (4, 4)]
    assert v._parse_keep("all", 6) == [(0, 5)]
    with pytest.raises(SystemExit):
        v._parse_keep("3-9", 6)
    w = [{"w": "Hi.", "s": 0, "e": 0.3}, {"w": "So", "s": 1.5, "e": 1.7}, {"w": "yes", "s": 1.7, "e": 2}]
    assert v._listing(w) == "[0:00.0] 0:Hi.\n   (pause 1.2s)\n[0:01.5] 1:So 2:yes"


def test_captions_highlight_the_word_being_said():
    reel = {"duration": 3, "style": {"accent": "#FF0000", "captions": {"perPage": 2}},
            "words": [{"w": "Hello", "s": 0.2, "e": 0.5}, {"w": "there.", "s": 0.5, "e": 0.9}, {"w": "Bye", "s": 1.5, "e": 1.8}]}
    events = [l for l in v._ass(reel).splitlines() if l.startswith("Dialogue")]
    assert len(events) == 3  # "HELLO THERE" twice (each word lit in turn), then "BYE"
    assert "\\c&H0000FF&" in events[0] and "}HELLO{" in events[0] and "THERE" in events[0]
    assert events[1].split(",")[1] == "0:00:00.47" and "}THERE{" in events[1]
    assert events[2].split(",")[2] == "0:00:02.20"  # the last page stays a moment after the word


def test_each_card_is_its_own_clip():
    reel = {"duration": 10, "scenes": [{"from": 1, "to": 3}, {"from": 4, "to": 4}], "end": {"from": 8, "title": "x"}}
    assert [(c["only"], c["a"], c["b"]) for c in v._cards(reel, 30)] == [(0, 30, 90), ("end", 240, 299)]
