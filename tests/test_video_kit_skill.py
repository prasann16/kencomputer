"""The video kit skill's ElevenLabs helper (skills/video-kit/scripts/eleven.py), with every HTTP call faked."""
import importlib.util
import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "video-kit" / "scripts" / "eleven.py"
spec = importlib.util.spec_from_file_location("eleven", SCRIPT)
eleven = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eleven)

KEY = "sk_test_never_print_me_1234567890"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAPI:
    """Answers urlopen by (method, path prefix); records every request."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        path = url.split("api.elevenlabs.io", 1)[-1]
        self.calls.append({"method": method, "path": path, "headers": dict(req.header_items()), "data": req.data, "url": url})
        for (m, prefix), answer in self.routes.items():
            if m == method and path.startswith(prefix):
                answer = answer(len(self.calls)) if callable(answer) else answer
                if isinstance(answer, int):
                    raise urllib.error.HTTPError(url, answer, "err", {}, io.BytesIO(b'{"detail":"nope"}'))
                return Resp(answer if isinstance(answer, bytes) else json.dumps(answer).encode())
        raise AssertionError(f"unexpected call {method} {path}")


HOST_VARS = ("KEN_ELEVEN", "KEN_ELEVEN_URL", "KEN_ELEVEN_TOKEN", "KEN_INCLUDED_AI_URL", "KEN_INCLUDED_AI_TOKEN")


@pytest.fixture
def api(monkeypatch):
    for name in HOST_VARS:  # their own key: a Ken with no host
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setattr(eleven, "duration", lambda p: 12.0)
    monkeypatch.setattr(eleven, "audio_only", lambda src: (src, False))  # the fake videos here have no real sound

    def install(routes):
        fake = FakeAPI(routes)
        monkeypatch.setattr(eleven.urllib.request, "urlopen", fake)
        return fake
    return install


def run(argv, capsys):
    code = eleven.main(argv)
    out, err = capsys.readouterr()
    assert KEY not in out and KEY not in err
    return code, (json.loads(out.strip().splitlines()[-1]) if out.strip() else None), err


def test_no_key_says_where_to_add_it(monkeypatch, capsys):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    code, _, err = run(["check"], capsys)
    assert code == 1 and "ELEVENLABS_API_KEY" in err and "Brain" in err


def test_check_reports_credits_left_and_sends_the_key_only_as_a_header(api, capsys):
    fake = api({("GET", "/v1/user/subscription"): {"tier": "starter", "character_count": 1000, "character_limit": 30000,
                                                  "can_use_instant_voice_cloning": True, "voice_slots_used": 1, "voice_limit": 10}})
    code, out, _ = run(["check"], capsys)
    assert code == 0 and out["credits_left"] == 29000 and out["can_clone"] is True
    assert fake.calls[0]["headers"]["Xi-api-key"] == KEY and KEY not in fake.calls[0]["url"]


def test_tts_writes_audio_and_prices_by_characters(api, tmp_path, capsys):
    fake = api({("POST", "/v1/text-to-speech/VOICE"): b"ID3audio"})
    out = tmp_path / "vo.mp3"
    code, info, _ = run(["tts", "--voice", "VOICE", "--text", "x" * 500, "--out", str(out)], capsys)
    assert code == 0 and out.read_bytes() == b"ID3audio" and info["usd"] == 0.04
    body = json.loads(fake.calls[0]["data"])
    assert body["model_id"] == "eleven_multilingual_v2" and "output_format=mp3_44100_128" in fake.calls[0]["path"]


def test_errors_are_explained_and_never_echo_the_key(api, tmp_path, capsys):
    api({("POST", "/v1/text-to-speech/"): 401})
    code, _, err = run(["tts", "--voice", "V", "--text", "hi", "--out", str(tmp_path / "a.mp3")], capsys)
    assert code == 1 and "401" in err and "Brain" in err


def test_subtitles_are_named_once_per_language():
    assert eleven._subs(Path("v/reel.hi.mp4"), "hi", "srt") == Path("v/reel.hi.srt")
    assert eleven._subs(Path("v/reel.mp4"), "hi", "srt") == Path("v/reel.hi.srt")
    assert eleven._subs(Path("v/reel.dub.mp4"), "es", "vtt") == Path("v/reel.dub.es.vtt")


def test_a_wrong_key_is_named_even_though_elevenlabs_says_400(api, tmp_path, capsys):
    detail = {"detail": {"type": "authentication_error", "code": "invalid_api_key", "message": "API key is invalid.",
                         "status": "invalid_api_key"}}
    api({("GET", "/v1/user/subscription"): lambda n: (_ for _ in ()).throw(
        urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(json.dumps(detail).encode())))})
    code, _, err = run(["check"], capsys)
    assert code == 1 and "key is wrong" in err and "Brain" in err and "API key is invalid." in err and "request_id" not in err


def test_out_of_credits_is_not_called_a_wrong_key(api, tmp_path, capsys):
    detail = {"detail": {"status": "quota_exceeded", "message": "This request exceeds your quota of 10000."}}
    api({("POST", "/v1/text-to-speech/"): lambda n: (_ for _ in ()).throw(
        urllib.error.HTTPError("u", 401, "q", {}, io.BytesIO(json.dumps(detail).encode())))})
    code, _, err = run(["tts", "--voice", "V", "--text", "hi", "--out", str(tmp_path / "a.mp3")], capsys)
    assert code == 1 and "out of credits" in err and "key is wrong" not in err and "exceeds your quota" in err


def test_clone_refuses_without_own_voice_or_consent(api, tmp_path, capsys):
    fake = api({})
    sample = tmp_path / "me.mp3"
    sample.write_bytes(b"a")
    code, _, err = run(["clone", "--name", "Me", "--sample", str(sample), "--consent", str(tmp_path / "consent.mp3"), "--own-voice"], capsys)
    assert code == 1 and "No file" in err
    (tmp_path / "consent.mp3").write_bytes(b"c")
    code, _, err = run(["clone", "--name", "Me", "--sample", str(sample), "--consent", str(tmp_path / "consent.mp3")], capsys)
    assert code == 1 and "own voice" in err
    assert fake.calls == []


def test_clone_uploads_every_sample_and_labels_the_consent(api, tmp_path, capsys):
    fake = api({("POST", "/v1/voices/add"): {"voice_id": "v123", "requires_verification": False}})
    a, b, c = (tmp_path / n for n in ("a.mp3", "b.mp3", "consent.mp3"))
    for f in (a, b, c):
        f.write_bytes(b"x")
    code, info, _ = run(["clone", "--name", "Me", "--sample", str(a), "--sample", str(b), "--consent", str(c), "--own-voice"], capsys)
    assert code == 0 and info["voice_id"] == "v123"
    data = fake.calls[0]["data"]
    assert data.count(b'name="files"') == 2 and b"consent.mp3" in data and b'name="consent' not in data


def test_clone_needs_ten_seconds_of_samples(api, tmp_path, capsys, monkeypatch):
    api({})
    monkeypatch.setattr(eleven, "duration", lambda p: 4.0)
    a, c = tmp_path / "a.mp3", tmp_path / "c.mp3"
    a.write_bytes(b"x"); c.write_bytes(b"x")
    code, _, err = run(["clone", "--name", "Me", "--sample", str(a), "--consent", str(c), "--own-voice"], capsys)
    assert code == 1 and "10 s" in err


def test_isolate_puts_clean_audio_back_into_a_video(api, tmp_path, capsys, monkeypatch):
    api({("POST", "/v1/audio-isolation"): b"clean"})
    swapped = []
    monkeypatch.setattr(eleven, "swap_audio", lambda v, au, out: swapped.append((v.name, au.read_bytes(), out.name)) or out)
    src = tmp_path / "talk.mp4"
    src.write_bytes(b"video")
    code, info, _ = run(["isolate", "--in", str(src), "--out", str(tmp_path / "clean.mp4")], capsys)
    assert code == 0 and swapped == [("talk.mp4", b"clean", "clean.mp4")] and info["usd"] == 0.024


def test_isolate_audio_in_audio_out(api, tmp_path, capsys):
    api({("POST", "/v1/audio-isolation"): b"clean"})
    src = tmp_path / "talk.m4a"
    src.write_bytes(b"a")
    code, _, _ = run(["isolate", "--in", str(src), "--out", str(tmp_path / "clean.mp3")], capsys)
    assert code == 0 and (tmp_path / "clean.mp3").read_bytes() == b"clean"


def test_voice_changer_uses_the_current_path_and_multilingual_model(api, tmp_path, capsys):
    fake = api({("POST", "/v1/voice-changer/V"): b"new"})
    src = tmp_path / "take.mp3"
    src.write_bytes(b"a")
    code, _, _ = run(["changer", "--voice", "V", "--in", str(src), "--out", str(tmp_path / "o.mp3")], capsys)
    assert code == 0 and b"eleven_multilingual_sts_v2" in fake.calls[0]["data"]


def test_sfx_clamps_duration(api, tmp_path, capsys):
    fake = api({("POST", "/v1/sound-generation"): b"boom"})
    run(["sfx", "--text", "boom", "--seconds", "90", "--out", str(tmp_path / "b.mp3")], capsys)
    assert json.loads(fake.calls[0]["data"])["duration_seconds"] == 30.0


def test_music_sends_length_in_ms(api, tmp_path, capsys):
    fake = api({("POST", "/v1/music"): b"song"})
    code, info, _ = run(["music", "--prompt", "lofi", "--seconds", "20", "--instrumental", "--out", str(tmp_path / "m.mp3")], capsys)
    body = json.loads(fake.calls[0]["data"])
    assert code == 0 and body["music_length_ms"] == 20000 and body["force_instrumental"] is True and info["usd"] == 0.05


WORDS = [
    {"text": "Hello", "start": 0.0, "end": 0.4, "type": "word"}, {"text": " ", "start": 0.4, "end": 0.5, "type": "spacing"},
    {"text": "everyone.", "start": 0.5, "end": 1.0, "type": "word"},
    {"text": "(laughs)", "start": 1.0, "end": 1.2, "type": "audio_event"},
    {"text": "This", "start": 2.0, "end": 2.2, "type": "word"}, {"text": "is", "start": 2.2, "end": 2.3, "type": "word"},
    {"text": "Ken", "start": 2.3, "end": 2.6, "type": "word"},
]


def test_words_to_srt_breaks_on_sentences_and_skips_events():
    srt = eleven.words_to_srt(WORDS)
    assert srt == "1\n00:00:00,000 --> 00:00:01,000\nHello everyone.\n\n2\n00:00:02,000 --> 00:00:02,600\nThis is Ken\n\n"


def test_words_to_srt_keeps_lines_short():
    words = [{"text": "word", "start": i * 0.2, "end": i * 0.2 + 0.15, "type": "word"} for i in range(20)]
    for cue in eleven.words_to_srt(words, max_chars=16).strip().split("\n\n"):
        assert len(cue.split("\n")[2]) <= 16


def test_captions_writes_srt_and_words(api, tmp_path, capsys):
    fake = api({("POST", "/v1/speech-to-text"): {"language_code": "hin", "text": "x", "words": WORDS, "audio_duration_secs": 60}})
    src = tmp_path / "reel.mp4"
    src.write_bytes(b"v")
    code, info, _ = run(["captions", "--in", str(src), "--out", str(tmp_path / "reel.srt"), "--lang", "hi"], capsys)
    assert code == 0 and "Hello everyone." in (tmp_path / "reel.srt").read_text()
    assert len(json.loads((tmp_path / "reel.words.json").read_text())["words"]) == 5
    assert b"scribe_v2" in fake.calls[0]["data"] and b'name="language_code"\r\n\r\nhi' in fake.calls[0]["data"]
    assert info["usd"] == round(0.2 / 60, 4)


def test_dub_v1_polls_then_downloads_video_and_subtitles(api, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(eleven.time, "sleep", lambda s: None)
    states = iter(["preparing", "dubbing", "dubbed"])
    fake = api({
        ("POST", "/v1/dubbing"): {"dubbing_id": "d1", "expected_duration_sec": 30},
        ("GET", "/v1/dubbing/d1/audio/hi"): b"mp4bytes",
        ("GET", "/v1/dubbing/d1/transcripts/hi/format/srt"): {"srt": "1\n00:00:00,000 --> 00:00:01,000\nनमस्ते\n"},
        ("GET", "/v1/dubbing/d1"): lambda n: {"status": next(states)},
    })
    src = tmp_path / "reel.mp4"
    src.write_bytes(b"v")
    code, info, _ = run(["dub", "--in", str(src), "--to", "hi", "--out", str(tmp_path / "reel.hi.mp4")], capsys)
    assert code == 0 and info["job"] == "v1:d1" and (tmp_path / "reel.hi.mp4").read_bytes() == b"mp4bytes"
    assert "नमस्ते" in (tmp_path / "reel.hi.srt").read_text() and info["usd"] == 0.1
    assert not (tmp_path / "reel.hi.hi.srt").exists()
    create = fake.calls[0]["data"]
    assert b'name="target_lang"\r\n\r\nhi' in create and b'name="watermark"\r\n\r\nfalse' in create


def test_dub_failure_is_reported_once(api, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(eleven.time, "sleep", lambda s: None)
    api({("POST", "/v1/dubbing"): {"dubbing_id": "d2"}, ("GET", "/v1/dubbing/d2"): {"status": "failed", "error": "no speech"}})
    src = tmp_path / "r.mp4"
    src.write_bytes(b"v")
    code, _, err = run(["dub", "--in", str(src), "--to", "ta", "--out", str(tmp_path / "o.mp4")], capsys)
    assert code == 1 and "no speech" in err and "v1:d2" in err


def test_dub_v2_adds_language_downloads_signed_audio_and_remuxes(api, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(eleven.time, "sleep", lambda s: None)
    monkeypatch.setattr(eleven, "download", lambda url, out: (out.write_bytes(url.encode()), out)[1])
    swapped = []
    monkeypatch.setattr(eleven, "swap_audio", lambda v, au, out: (swapped.append((v.name, au.name)), out.write_bytes(b"muxed"), out)[2])
    fake = api({
        ("POST", "/v1/dubbing/project/p1/language"): {"language_id": "l1"},
        ("POST", "/v1/dubbing/project"): {"project_id": "p1", "status": "queued", "language_ids": []},
        ("GET", "/v1/dubbing/project/p1/language/l1"): lambda n: (
            {"status": "processing"} if n < 4 else
            {"status": "completed", "outputs": {"lossless_audio": "https://signed/a.flac", "subtitles": "https://signed/s.vtt"}}),
    })
    src = tmp_path / "reel.mp4"
    src.write_bytes(b"v")
    code, info, _ = run(["dub", "--in", str(src), "--to", "es", "--out", str(tmp_path / "reel.es.mp4"), "--v2"], capsys)
    assert code == 0 and info["job"] == "v2:p1:l1" and swapped == [("reel.mp4", "reel.es.dub.flac")]
    assert (tmp_path / "reel.es.vtt").read_text() == "https://signed/s.vtt" and info["usd"] == 0.44
    assert b"dubbing_v2" in fake.calls[0]["data"]


def test_dub_status_hides_signed_links(api, capsys):
    api({("GET", "/v1/dubbing/project/p/language/l"): {"status": "completed", "outputs": {"lossless_audio": "https://secret"}}})
    code, info, _ = run(["dub-status", "v2:p:l"], capsys)
    assert code == 0 and info["state"] == "done" and "outputs" not in info


def test_design_saves_previews(api, tmp_path, capsys):
    import base64
    api({("POST", "/v1/text-to-voice/design"): {"text": "hi", "previews": [
        {"audio_base_64": base64.b64encode(b"p1").decode(), "generated_voice_id": "g1"},
        {"audio_base_64": base64.b64encode(b"p2").decode(), "generated_voice_id": "g2"}]}})
    code, info, _ = run(["design", "--describe", "calm narrator, slow and warm", "--out-dir", str(tmp_path / "pv")], capsys)
    assert code == 0 and [p["generated_voice_id"] for p in info["previews"]] == ["g1", "g2"]
    assert (tmp_path / "pv" / "preview-2.mp3").read_bytes() == b"p2"


def test_estimate_costs_without_calling_the_api(api, tmp_path, capsys):
    fake = api({})
    src = tmp_path / "r.mp4"
    src.write_bytes(b"v")
    code, info, _ = run(["estimate", "dub", str(src)], capsys)
    assert code == 0 and info["usd"] == 0.1 and fake.calls == []
    _, info, _ = run(["estimate", "tts", "--chars", "2000"], capsys)
    assert info["usd"] == 0.16


def test_swap_audio_with_real_ffmpeg(tmp_path):
    ff = ["ffmpeg", "-v", "error", "-y"]
    try:
        subprocess.run(ff + ["-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-f", "lavfi", "-i", "sine=f=220:d=1",
                             "-shortest", str(tmp_path / "v.mp4")], check=True)
        subprocess.run(ff + ["-f", "lavfi", "-i", "sine=f=880:d=1", str(tmp_path / "a.mp3")], check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no ffmpeg")
    out = eleven.swap_audio(tmp_path / "v.mp4", tmp_path / "a.mp3", tmp_path / "o.mp4")
    assert out.stat().st_size > 0 and 0.5 < eleven.duration(out) < 1.5


def test_ken_runs_the_helper_with_its_own_python(tmp_path):
    import os
    import sys
    env = {**os.environ, "KEN_HOME": str(tmp_path)}
    for name in ("ELEVENLABS_API_KEY", *HOST_VARS):
        env.pop(name, None)
    bot = SCRIPT.parent.parent.parent.parent / "bot.py"
    r = subprocess.run([sys.executable, str(bot), "--eleven", "estimate", "tts", "--chars", "1000"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)["usd"] == 0.08
    r = subprocess.run([sys.executable, str(bot), "--eleven", "check"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 1 and "ELEVENLABS_API_KEY" in r.stderr


# ---------- hosted Ken: through the host's /box/eleven on Ken's own ElevenLabs account (ken-hosted src/eleven.ts)

TOKEN = "ai_token_of_this_computer_123"


class HostResp(Resp):
    def __init__(self, data, left=None):
        super().__init__(data)
        self.headers = {"x-ken-quota-left": left} if left else {}


class FakeHost(FakeAPI):
    """The host: same paths as ElevenLabs under /box/eleven, answers may carry x-ken-quota-left."""

    def __call__(self, req, timeout=None):
        url = req.full_url
        assert url.startswith("https://app.example.dev/box/eleven/"), url
        path = url.split("/box/eleven", 1)[1]
        if path == "/quota" and ("GET", "/quota") not in self.routes:  # the look before an upload: the route is live
            return HostResp(json.dumps(QUOTA).encode())
        self.calls.append({"method": req.get_method(), "path": path, "headers": dict(req.header_items()), "data": req.data, "url": url})
        for (m, prefix), answer in self.routes.items():
            if m == req.get_method() and path.startswith(prefix):
                answer = answer(len(self.calls)) if callable(answer) else answer
                if isinstance(answer, tuple) and isinstance(answer[0], int):
                    raise urllib.error.HTTPError(url, answer[0], "err", {}, io.BytesIO(json.dumps(answer[1]).encode()))
                left = None
                if isinstance(answer, tuple):
                    answer, left = answer
                return HostResp(answer if isinstance(answer, bytes) else json.dumps(answer).encode(), left)
        raise AssertionError(f"unexpected call {req.get_method()} {path}")


QUOTA = {"period": "2026-11", "resets": "on the 1st of next month (UTC)", "features": {
    "tts": {"unit": "chars", "limit": 20000, "used": 500, "left": 19500},
    "dub": {"unit": "seconds", "limit": 600, "used": 60, "left": 540},
    "clone": {"unit": "voices", "limit": 2, "used": 0, "left": 2}}}


@pytest.fixture
def hosted(monkeypatch):
    for name in HOST_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://app.example.dev/ai")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", TOKEN)
    monkeypatch.setattr(eleven, "duration", lambda p: 12.0)
    eleven.QUOTA_LEFT.clear()

    def install(routes):
        fake = FakeHost(routes)
        monkeypatch.setattr(eleven.urllib.request, "urlopen", fake)
        return fake
    return install


def hrun(argv, capsys):
    code = eleven.main(argv)
    out, err = capsys.readouterr()
    assert TOKEN not in out and TOKEN not in err
    return code, (json.loads(out.strip().splitlines()[-1]) if out.strip() else None), err


def test_host_is_the_default_and_uses_the_computers_token_not_a_key(hosted, tmp_path, capsys):
    fake = hosted({("POST", "/v1/text-to-speech/V"): (b"ID3audio", "19489 chars")})
    code, info, _ = hrun(["tts", "--voice", "V", "--text", "hello world", "--out", str(tmp_path / "vo.mp3")], capsys)
    assert code == 0 and (tmp_path / "vo.mp3").read_bytes() == b"ID3audio"
    sent = fake.calls[0]["headers"]
    assert sent["Authorization"] == f"Bearer {TOKEN}" and "Xi-api-key" not in sent and sent["User-agent"] == "Ken"
    assert "usd" not in info and info["quota_left"] == "19489 chars"  # no prices on Ken's account


def test_host_wins_over_their_own_key_unless_they_choose_it(hosted, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert eleven.host() == ("https://app.example.dev/box/eleven", TOKEN)
    monkeypatch.setenv("KEN_ELEVEN", "own")
    assert eleven.host() is None
    monkeypatch.delenv("KEN_ELEVEN")
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "http://evil.example/ai")  # never a plain-http host
    assert eleven.host() is None
    monkeypatch.setenv("KEN_ELEVEN_URL", "http://127.0.0.1:8787/box/eleven")  # a local test copy is fine
    assert eleven.host() == ("http://127.0.0.1:8787/box/eleven", TOKEN)


def test_check_on_the_host_shows_whats_left_not_money(hosted, capsys):
    hosted({("GET", "/quota"): QUOTA})
    code, info, _ = hrun(["check"], capsys)
    assert code == 0 and info["account"] == "ken" and info["left"]["dub"] == "9 min" and info["can_clone"] is True
    assert "usd" not in json.dumps(info) and "credits" not in json.dumps(info)


def test_estimate_on_the_host_says_if_it_fits(hosted, tmp_path, capsys):
    hosted({("GET", "/quota"): QUOTA})
    src = tmp_path / "r.mp4"
    src.write_bytes(b"v")
    code, info, _ = hrun(["estimate", "dub", str(src)], capsys)
    assert code == 0 and info == {"feature": "dub", "needs": "0.2 min", "left": "9 min", "fits": True}


def test_quota_reached_is_one_clear_line(hosted, tmp_path, capsys):
    hosted({("POST", "/v1/text-to-speech/"): (402, {"code": "quota", "error": "That needs 900 chars of tts, and 10 chars are left this month."})})
    code, _, err = hrun(["tts", "--voice", "V", "--text", "x" * 900, "--out", str(tmp_path / "a.mp3")], capsys)
    assert code == 1 and "Allowance used up" in err and "10 chars are left" in err and "$" not in err


def test_eleven_down_says_dont_loop(hosted, tmp_path, capsys):
    hosted({("POST", "/v1/sound-generation"): (502, {"code": "eleven_down", "error": "ElevenLabs isn't answering right now."})})
    code, _, err = hrun(["sfx", "--text", "boom", "--out", str(tmp_path / "b.mp3")], capsys)
    assert code == 1 and "isn't answering" in err and "loop" in err


def test_host_uploads_send_their_length_and_a_video_goes_up_as_audio(hosted, tmp_path, capsys, monkeypatch):
    fake = hosted({("POST", "/v1/audio-isolation"): (b"clean", "1788 seconds")})
    sent_audio = tmp_path / "x" / "talk.audio.mp3"

    def fake_audio_only(src):
        sent_audio.parent.mkdir()
        sent_audio.write_bytes(b"just-audio")
        return sent_audio, True
    monkeypatch.setattr(eleven, "audio_only", fake_audio_only)
    swapped = []
    monkeypatch.setattr(eleven, "swap_audio", lambda v, au, out: swapped.append((v.name, au.read_bytes())) or out)
    src = tmp_path / "talk.mp4"
    src.write_bytes(b"the whole video")
    code, info, _ = hrun(["isolate", "--in", str(src), "--out", str(tmp_path / "clean.mp4")], capsys)
    call = fake.calls[0]
    assert code == 0 and call["headers"]["X-ken-seconds"] == "12.00"
    assert b"just-audio" in call["data"] and b"the whole video" not in call["data"]
    assert swapped == [("talk.mp4", b"clean")] and info["quota_left"] == "29.8 min"
    assert not sent_audio.parent.exists()  # the temporary audio is gone


def test_audio_only_with_real_ffmpeg(tmp_path):
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-f", "lavfi",
                        "-i", "sine=f=220:d=1", "-shortest", str(tmp_path / "v.mp4")], check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no ffmpeg")
    out, tmp = eleven.audio_only(tmp_path / "v.mp4")
    assert tmp and out.suffix == ".mp3" and out.stat().st_size > 0
    eleven._drop(out, tmp)
    assert not out.exists()


def test_host_clone_sends_consent(hosted, tmp_path, capsys):
    fake = hosted({("POST", "/v1/voices/add"): ({"voice_id": "v9"}, "1 voices")})
    a, c = tmp_path / "a.mp3", tmp_path / "consent.mp3"
    a.write_bytes(b"x"); c.write_bytes(b"c")
    code, info, _ = hrun(["clone", "--name", "Me", "--sample", str(a), "--consent", str(c), "--own-voice"], capsys)
    h = fake.calls[0]["headers"]
    assert code == 0 and info["voice_id"] == "v9" and h["X-ken-consent"] == "own-voice" and h["X-ken-consent-note"] == "consent.mp3"


def test_host_dub_goes_up_as_audio_and_comes_back_over_the_original_picture(hosted, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(eleven.time, "sleep", lambda s: None)
    monkeypatch.setattr(eleven, "audio_only", lambda src: (src.with_name("reel.audio.mp3"), False))
    (tmp_path / "reel.audio.mp3").write_bytes(b"a")
    swapped = []
    monkeypatch.setattr(eleven, "swap_audio", lambda v, au, out: (swapped.append((v.name, au.read_bytes())), out.write_bytes(b"muxed"), out)[2])
    fake = hosted({
        ("POST", "/v1/dubbing"): ({"dubbing_id": "d1", "expected_duration_sec": 12}, "528 seconds"),
        ("GET", "/v1/dubbing/d1/audio/hi"): b"dubbed-mp3",
        ("GET", "/v1/dubbing/d1/transcripts/hi/format/srt"): {"srt": ""},
        ("GET", "/v1/dubbing/d1"): {"status": "dubbed"},
    })
    src = tmp_path / "reel.mp4"
    src.write_bytes(b"v")
    code, info, _ = hrun(["dub", "--in", str(src), "--to", "hi", "--out", str(tmp_path / "reel.hi.mp4")], capsys)
    assert code == 0 and info["job"] == "v1:d1:a" and swapped == [("reel.mp4", b"dubbed-mp3")]
    assert (tmp_path / "reel.hi.mp4").read_bytes() == b"muxed" and info["quota_left"] == "8.8 min"
    assert fake.calls[0]["headers"]["X-ken-seconds"] == "12.00" and b'name="name"\r\n\r\nreel\r\n' in fake.calls[0]["data"]


def test_voice_delete(hosted, capsys):
    fake = hosted({("DELETE", "/v1/voices/v9"): {"status": "ok"}})
    code, info, _ = hrun(["voice-delete", "v9"], capsys)
    assert code == 0 and info == {"deleted": "v9"} and fake.calls[0]["method"] == "DELETE"


class Redirected(HostResp):
    """What the host answers before /box/eleven is deployed: a redirect to its homepage, followed by urllib."""

    def __init__(self, url):
        super().__init__(b"<!doctype html><title>Ken</title>")
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self._url = url

    def geturl(self):
        return self._url


def not_live(monkeypatch, then=None):
    """The host redirects every /box/eleven call to its homepage; ElevenLabs itself answers `then`."""
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        if "/box/eleven" in req.full_url:
            return Redirected("https://app.example.dev/")
        return then(req, timeout) if then else Resp(b"{}")
    monkeypatch.setattr(eleven.urllib.request, "urlopen", urlopen)
    return calls


def test_host_not_live_and_no_key_says_so_in_one_line(hosted, monkeypatch, tmp_path, capsys):
    eleven.HOST_NOT_LIVE.clear()
    not_live(monkeypatch)
    code, _, err = hrun(["check"], capsys)
    assert code == 1 and "isn't switched on" in err and "ELEVENLABS_API_KEY" in err and "Traceback" not in err
    code, _, err = hrun(["tts", "--voice", "V", "--text", "hi", "--out", str(tmp_path / "a.mp3")], capsys)
    assert code == 1 and not (tmp_path / "a.mp3").exists()  # never a web page saved as audio


def test_host_not_live_runs_on_their_own_key(hosted, monkeypatch, tmp_path, capsys):
    eleven.HOST_NOT_LIVE.clear()
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    seen = []

    def eleven_labs(req, timeout):
        seen.append(dict(req.header_items()))
        return Resp(b"ID3audio")
    calls = not_live(monkeypatch, eleven_labs)
    try:
        code, info, _ = hrun(["tts", "--voice", "V", "--text", "hello", "--out", str(tmp_path / "vo.mp3")], capsys)
    finally:
        eleven.HOST_NOT_LIVE.clear()
    assert code == 0 and (tmp_path / "vo.mp3").read_bytes() == b"ID3audio"
    assert calls[0].startswith("https://app.example.dev/box/eleven/") and calls[1].startswith("https://api.elevenlabs.io/")
    assert seen[0]["Xi-api-key"] == KEY and "Authorization" not in seen[0]
    assert info["account"] == "own" and info["usd"] == 0.0004 and KEY not in json.dumps(info)


def test_check_and_estimate_fall_back_to_their_plan_when_the_host_route_isnt_live(hosted, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    paths = []

    def eleven_labs(req, timeout):
        paths.append(req.full_url.split("api.elevenlabs.io", 1)[1])
        return Resp(json.dumps({"tier": "starter", "character_count": 10, "character_limit": 30000}).encode())
    for argv in (["check"], ["estimate", "tts", "--chars", "1000"]):
        eleven.HOST_NOT_LIVE.clear()
        not_live(monkeypatch, eleven_labs)
        try:
            code, info, _ = hrun(argv, capsys)
        finally:
            eleven.HOST_NOT_LIVE.clear()
        assert code == 0 and info["account"] == "own"
    assert paths == ["/v1/user/subscription"]  # never /quota on ElevenLabs itself


def test_host_errors_from_its_route_are_not_mistaken_for_a_missing_route(hosted, tmp_path, capsys):
    hosted({("POST", "/v1/music"): (404, {"code": "not_allowed", "error": "The video kit can't do that."})})
    code, _, err = hrun(["music", "--prompt", "x", "--out", str(tmp_path / "m.mp3")], capsys)
    assert code == 1 and "can't do that" in err and "isn't switched on" not in err


def test_a_redirect_urllib_wont_follow_is_the_route_not_being_live(hosted, monkeypatch, capsys):
    eleven.HOST_NOT_LIVE.clear()

    def urlopen(req, timeout=None):  # urllib raises a DELETE's 302 instead of following it
        raise urllib.error.HTTPError(req.full_url, 302, "Found", {"location": "/"}, io.BytesIO(b""))
    monkeypatch.setattr(eleven.urllib.request, "urlopen", urlopen)
    code, _, err = hrun(["voice-delete", "v9"], capsys)
    assert code == 1 and "isn't switched on" in err and "(302)" not in err


def test_a_file_ffmpeg_cant_read_is_one_line_and_leaves_nothing_behind(tmp_path, capsys):
    if not eleven.shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    (tmp_path / "notes.mp4").write_text("not a video")
    with pytest.raises(eleven.Fail) as e:
        eleven.audio_only(tmp_path / "notes.mp4")
    assert "notes.mp4" in e.value.msg and capsys.readouterr().err == ""


def test_one_speaker_gets_no_speaker_labels_two_do():
    one = [dict(w, speaker_id="speaker_0") for w in WORDS]
    assert "[speaker_0]" not in eleven.words_to_srt(one, speakers=True)
    two = one[:3] + [dict(w, speaker_id="speaker_1") for w in one[3:]]
    srt = eleven.words_to_srt(two, speakers=True)
    assert "[speaker_0] Hello everyone." in srt and "[speaker_1] This is Ken" in srt


def test_a_voice_description_under_20_characters_is_caught_before_the_call(api, tmp_path, capsys):
    fake = api({})
    code, _, err = run(["design-save", "--id", "g1", "--name", "N", "--describe", "test"], capsys)
    assert code == 1 and "20 characters" in err and fake.calls == []


def test_an_upload_isnt_sent_twice_when_the_route_isnt_live(hosted, monkeypatch, tmp_path, capsys):
    eleven.HOST_NOT_LIVE.clear()
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    sent = []

    def urlopen(req, timeout=None):
        sent.append((req.get_method(), req.full_url.split("/", 3)[3], len(req.data or b"")))
        if "/box/eleven" in req.full_url:
            return Redirected("https://app.example.dev/")
        return Resp(b"clean")
    monkeypatch.setattr(eleven.urllib.request, "urlopen", urlopen)
    src = tmp_path / "talk.m4a"
    src.write_bytes(b"a" * 5000)
    try:
        code, info, _ = hrun(["isolate", "--in", str(src), "--out", str(tmp_path / "clean.m4a")], capsys)
    finally:
        eleven.HOST_NOT_LIVE.clear()
    assert code == 0 and info["account"] == "own"
    assert [(m, path) for m, path, _ in sent] == [("GET", "box/eleven/quota"), ("POST", "v1/audio-isolation")]
