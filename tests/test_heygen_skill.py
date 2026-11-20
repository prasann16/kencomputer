"""The heygen skill's helper against a fake HeyGen (and a fake Ken host) on localhost. No real key, no live calls."""
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "heygen"
spec = importlib.util.spec_from_file_location("heygen_skill", SKILL / "heygen.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

KEY = "sk_test_their_own_key_123"
TOKEN = "box-token-abcdef"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"v" * 4000


class Fake:
    """HeyGen's v3 API as the docs describe it, plus hosted Ken's /box/heygen when asked through that path."""

    def __init__(self):
        self.seen: list[dict] = []
        self.looks = [{"id": "look_me", "name": "Swap desk", "avatar_type": "digital_twin", "group_id": "g1",
                       "preferred_orientation": "portrait", "default_voice_id": "voice_me", "status": "completed"}]
        self.groups = [{"id": "g1", "name": "Swap", "created_at": 1, "looks_count": 1, "consent_status": "accepted", "status": "completed"}]
        self.voices = [{"voice_id": "voice_me", "mode": "instant", "name": "Swap", "status": "ACTIVE", "created_at": 1}]
        self.polls_before_done = 1
        self.video = {"status": "completed", "duration": 31.5}
        self.create_error: tuple[int, dict, dict] | None = None
        self.me = {"billing_type": "wallet", "wallet": {"currency": "usd", "remaining_balance": 12.5}, "subscription": None, "usage_based": None}
        self.quota = {"features": {"avatar_video": {"left": 240, "unit": "seconds", "limit": 300}}, "resets": "2026-12-01"}
        self.host_live = True
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def answer(self, code, body, headers=None, raw=False):
                data = body if raw else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "video/mp4" if raw else "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def handle_one(self, method):
                url = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                size = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(size)) if size else None
                path, via_host = url.path, url.path.startswith("/box/heygen")
                fake.seen.append({"method": method, "path": path, "query": q, "body": body, "headers": dict(self.headers.items())})
                if path == "/files/video.mp4":
                    return self.answer(200, MP4, raw=True)
                if via_host:
                    if not fake.host_live:
                        return self.answer(404, {"error": "Not found"})
                    if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                        return self.answer(401, {"error": "This computer isn't recognised.", "code": "unknown_computer"})
                    path = path[len("/box/heygen"):]
                    if path == "/quota":
                        return self.answer(200, fake.quota)
                elif self.headers.get("X-Api-Key") != KEY:
                    return self.answer(401, {"error": {"code": "unauthorized", "message": "Invalid API key"}})
                left = {"x-ken-quota-left": "209 seconds"} if via_host else {}
                if path == "/v3/users/me":
                    return self.answer(200, {"data": fake.me})
                if path == "/v3/avatars/looks":
                    if q.get("ownership") == "public":
                        return self.answer(200, {"data": [{"id": "stock_1", "name": "Anna", "avatar_type": "studio_avatar"}], "has_more": False, "next_token": None})
                    if not q.get("token") and len(fake.looks) > 1:  # two pages
                        return self.answer(200, {"data": fake.looks[:1], "has_more": True, "next_token": "p2"})
                    return self.answer(200, {"data": fake.looks[1:] if q.get("token") else fake.looks, "has_more": False, "next_token": None})
                if path == "/v3/avatars":
                    return self.answer(200, {"data": fake.groups, "has_more": False, "next_token": None})
                if path == "/v3/models/audio/voices":
                    stock = [{"voice_id": "stock_voice", "mode": "professional", "name": "Calm Ava", "status": "ACTIVE", "created_at": 1, "language": "en"}]
                    return self.answer(200, {"data": stock if q.get("type") == "public" else fake.voices, "has_more": False})
                if path == "/v3/videos" and method == "POST":
                    if fake.create_error:
                        code, said, headers = fake.create_error
                        return self.answer(code, said, headers)
                    return self.answer(200, {"data": {"video_id": "vid_1", "status": "waiting"}}, left)
                if path == "/v3/videos/vid_1":
                    polls = sum(1 for s in fake.seen if s["path"].endswith("/v3/videos/vid_1"))
                    if polls <= fake.polls_before_done:
                        return self.answer(200, {"data": {"id": "vid_1", "status": "processing"}})
                    port = self.server.server_address[1]
                    return self.answer(200, {"data": {"id": "vid_1", **fake.video, "video_url": f"http://127.0.0.1:{port}/files/video.mp4?Expires=1&Signature=secretsig"}})
                return self.answer(404, {"error": {"code": "not_found", "message": "No such thing"}})

            def do_GET(self):
                self.handle_one("GET")

            def do_POST(self):
                self.handle_one("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def calls(self, method=None, ends=None):
        return [s for s in self.seen if (not method or s["method"] == method) and (not ends or s["path"].endswith(ends))]


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.server.shutdown()
    f.server.server_close()


@pytest.fixture
def own(fake, monkeypatch):
    """Their own key, no host."""
    for name in ("KEN_HEYGEN_URL", "KEN_HEYGEN_TOKEN", "KEN_INCLUDED_AI_URL", "KEN_INCLUDED_AI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HEYGEN_API_KEY", KEY)
    monkeypatch.setenv("HEYGEN_BASE_URL", fake.url)
    h.QUOTA_LEFT.clear()
    return fake


@pytest.fixture
def hosted(fake, monkeypatch):
    """Hosted Ken, no key of their own."""
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)
    monkeypatch.delenv("KEN_INCLUDED_AI_URL", raising=False)
    monkeypatch.setenv("HEYGEN_BASE_URL", "http://127.0.0.1:9")  # HeyGen itself must never be called
    monkeypatch.setenv("KEN_HEYGEN_URL", fake.url + "/box/heygen")
    monkeypatch.setenv("KEN_HEYGEN_TOKEN", TOKEN)
    h.QUOTA_LEFT.clear()
    return fake


def run(capsys, *argv):
    """(exit code, the JSON line it printed or None, what it said went wrong)."""
    code = h.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err.strip()


def generate(capsys, out, *more):
    return run(capsys, "generate", "--avatar", "look_me", "--script", "So I built a thing. It works.", "--said", "yes thats my avatar",
               "--own-avatar", "--out", out, "--every", "0.01", *more)


def test_the_skill_says_what_it_is_for_and_whose_avatar():
    text = (SKILL / "SKILL.md").read_text()
    head = text.split("---")[1]
    assert "name: heygen" in head and "description:" in head
    assert "Their own avatar only" in text and "HEYGEN_API_KEY" in text and "9:16" in text
    assert "wait for" in text.lower()  # show first: nothing is made or posted on a guess


def test_their_avatars_are_only_their_own_with_consent_shown(own, capsys):
    own.looks.append({"id": "look_new", "name": "Swap couch", "avatar_type": "digital_twin", "group_id": "g2"})
    own.groups.append({"id": "g2", "name": "Couch", "created_at": 2, "looks_count": 1, "consent_status": "pending", "status": "pending_consent"})
    code, out, _ = run(capsys, "avatars")
    assert code == 0 and out["account"] == "own"
    assert [a["id"] for a in out["avatars"]] == ["look_me", "look_new"]  # both pages
    assert out["avatars"][0]["ready"] is True
    assert out["avatars"][1]["ready"] is False and out["avatars"][1]["waiting_on"] == "consent"
    assert all(c["query"]["ownership"] == "private" for c in own.calls(ends="/v3/avatars/looks"))


def test_stock_avatars_and_voices_are_listed_apart_and_marked(own, capsys):
    _, out, _ = run(capsys, "avatars", "--stock")
    assert out["stock"] is True and out["avatars"][0]["id"] == "stock_1" and "not them" in out["note"]
    _, out, _ = run(capsys, "voices", "--stock", "--language", "en", "--query", "calm")
    assert out["voices"][0]["id"] == "stock_voice"
    assert own.calls(ends="/voices")[-1]["query"] == {"type": "public", "limit": "50", "language": "en", "query": "calm"}
    _, out, _ = run(capsys, "voices")
    assert out["stock"] is False and out["voices"] == [{"id": "voice_me", "name": "Swap", "language": None, "gender": None, "kind": "instant", "ready": True}]


def test_a_video_is_made_vertical_waited_for_and_downloaded(own, capsys, tmp_path):
    out_file = tmp_path / "videos" / "x" / "avatar.mp4"
    code, out, err = generate(capsys, out_file)
    assert code == 0, err
    sent = own.calls("POST", "/v3/videos")[0]
    assert sent["body"] == {"type": "avatar", "avatar_id": "look_me", "script": "So I built a thing. It works.",
                            "aspect_ratio": "9:16", "resolution": "1080p"}  # the avatar's own voice: no voice_id
    assert sent["headers"]["X-Api-Key"] == KEY and sent["headers"]["Idempotency-Key"].startswith("ken-")
    assert out_file.read_bytes() == MP4 and not out_file.with_name("avatar.mp4.part").exists()
    assert out["file"] == str(out_file) and out["video_id"] == "vid_1" and out["seconds"] == 31.5 and out["bytes"] == len(MP4)
    assert len(own.calls("GET", "/v3/videos/vid_1")) == 2  # processing, then completed
    assert "X-Api-Key" not in own.calls(ends="/files/video.mp4")[0]["headers"]  # a signed link: the key doesn't go with it
    note = json.loads(out_file.with_suffix(".heygen.json").read_text())
    assert note["video_id"] == "vid_1" and note["said"] == "yes thats my avatar" and note["own_avatar"] is True


def test_the_same_request_twice_is_one_video(own, capsys, tmp_path):
    generate(capsys, tmp_path / "a.mp4")
    generate(capsys, tmp_path / "b.mp4")
    first, second = (c["headers"]["Idempotency-Key"] for c in own.calls("POST", "/v3/videos"))
    assert first == second


def test_no_video_without_their_yes(own, capsys, tmp_path):
    code, out, err = run(capsys, "generate", "--avatar", "look_me", "--script", "hi there", "--out", tmp_path / "a.mp4")
    assert code == 1 and out is None and "Only their own avatar" in err
    assert not own.seen  # nothing was asked of HeyGen at all


def test_a_stock_or_someone_elses_avatar_is_refused(own, capsys, tmp_path):
    code, _, err = run(capsys, "generate", "--avatar", "stock_1", "--script", "hi there", "--said", "yes", "--own-avatar", "--out", tmp_path / "a.mp4")
    assert code == 1 and "isn't one of their own" in err
    assert not own.calls("POST")


def test_a_voice_that_isnt_theirs_is_refused(own, capsys, tmp_path):
    code, _, err = generate(capsys, tmp_path / "a.mp4", "--voice", "stock_voice")
    assert code == 1 and "voice isn't one of their own" in err and not own.calls("POST")
    code, _, _ = generate(capsys, tmp_path / "a.mp4", "--voice", "voice_me")
    assert code == 0 and own.calls("POST")[0]["body"]["voice_id"] == "voice_me"


def test_an_avatar_waiting_on_consent_makes_nothing(own, capsys, tmp_path):
    own.groups[0].update(consent_status="pending", status="pending_consent")
    code, _, err = generate(capsys, tmp_path / "a.mp4")
    assert code == 1 and "isn't ready yet" in err and not own.calls("POST")


def test_a_script_too_long_is_caught_before_anything_is_spent(own, capsys, tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("word " * 1200)
    code, _, err = run(capsys, "generate", "--avatar", "look_me", "--script-file", script, "--said", "yes", "--own-avatar", "--out", tmp_path / "a.mp4")
    assert code == 1 and "5000" in err and not own.seen


def test_out_of_credits_on_their_key_says_top_up_and_never_uses_kens_account(own, capsys, tmp_path, monkeypatch):
    own.create_error = (402, {"error": {"code": "insufficient_credit", "message": "Not enough credit"}}, {})
    code, _, err = generate(capsys, tmp_path / "a.mp4")
    assert code == 1 and err.startswith(h.OWN_CREDITS_GONE) and "top up on app.heygen.com" in err and "Ken's included" not in err
    # On hosted Ken too: their key still wins, the message offers Ken's allowance, and the host is never called.
    monkeypatch.setenv("KEN_HEYGEN_URL", own.url + "/box/heygen")
    monkeypatch.setenv("KEN_HEYGEN_TOKEN", TOKEN)
    code, _, err = generate(capsys, tmp_path / "a.mp4")
    assert code == 1 and "remove HEYGEN_API_KEY from Brain" in err
    assert not [s for s in own.seen if s["path"].startswith("/box/")]
    assert len(own.calls("POST")) == 2  # one try each: no retries


def test_a_429_is_told_apart_out_of_quota_or_too_fast(own, capsys, tmp_path):
    own.create_error = (429, {"error": {"code": "quota_exceeded", "message": "Free tier limit"}}, {})
    _, _, err = generate(capsys, tmp_path / "a.mp4")
    assert err.startswith(h.OWN_CREDITS_GONE)
    own.create_error = (429, {"error": {"code": "rate_limit_exceeded", "message": "Slow down"}}, {"Retry-After": "7"})
    _, _, err = generate(capsys, tmp_path / "a.mp4")
    assert "wait 7 seconds" in err and "loop" in err


def test_a_wrong_key_is_one_plain_line_and_the_key_is_never_shown(own, capsys, monkeypatch):
    monkeypatch.setenv("HEYGEN_API_KEY", "sk_wrong_key_999")
    code, out, err = run(capsys, "check")
    assert code == 1 and "check HEYGEN_API_KEY in Brain" in err and "sk_wrong_key_999" not in err


def test_no_key_and_no_host_says_where_to_add_it(fake, capsys, monkeypatch):
    for name in ("HEYGEN_API_KEY", "KEN_HEYGEN_URL", "KEN_HEYGEN_TOKEN", "KEN_INCLUDED_AI_URL", "KEN_INCLUDED_AI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HEYGEN_BASE_URL", fake.url)
    code, _, err = run(capsys, "avatars")
    assert code == 1 and "Brain tab" in err and "HEYGEN_API_KEY" in err and not fake.seen


def test_a_failed_video_is_reported_once_with_why(own, capsys, tmp_path):
    own.video = {"status": "failed", "failure_code": "rendering_failed", "failure_message": "Could not render"}
    code, _, err = generate(capsys, tmp_path / "a.mp4")
    assert code == 1 and "rendering_failed: Could not render" in err and not (tmp_path / "a.mp4").exists()
    assert len(own.calls("POST")) == 1
    _, out, _ = run(capsys, "status", "vid_1")
    assert out["status"] == "failed" and out["why"] == "rendering_failed: Could not render"


def test_slow_is_not_failed_and_get_picks_it_up_without_making_it_again(own, capsys, tmp_path):
    own.polls_before_done = 3
    code, _, err = generate(capsys, tmp_path / "a.mp4", "--timeout", "0.015")
    assert code == 1 and "Don't generate it again" in err and "get vid_1" in err
    code, out, _ = run(capsys, "get", "vid_1", "--out", tmp_path / "a.mp4", "--every", "0.01")
    assert code == 0 and (tmp_path / "a.mp4").read_bytes() == MP4
    assert len(own.calls("POST")) == 1  # made once
    code, _, err = run(capsys, "get", "vid_1", "--out", tmp_path / "a.mp4")
    assert code == 1 and "already there" in err  # their file is never overwritten by accident


def test_status_never_prints_the_signed_link(own, capsys):
    own.polls_before_done = 0
    code = h.main(["status", "vid_1"])
    out = capsys.readouterr().out
    assert code == 0 and json.loads(out)["status"] == "completed" and "secretsig" not in out and "http" not in out


def test_estimate_on_their_key_gives_the_length_and_their_balance(own, capsys):
    _, out, _ = run(capsys, "estimate", "--script", "word " * 75)
    assert out["seconds"] == 30 and out["words"] == 75 and out["balance"] == "12.5 usd" and out["account"] == "own"
    own.me = {"billing_type": "subscription", "subscription": {"plan": "creator", "credits": {"premium_credits": {"remaining": 40}}}}
    _, out, _ = run(capsys, "check")
    assert out["plan"] == "creator" and out["credits"] == {"premium_credits": 40}


# ---------- hosted Ken: /box/heygen ----------

def test_on_hosted_ken_calls_go_through_the_host_with_its_token(hosted, capsys, tmp_path):
    code, out, err = generate(capsys, tmp_path / "a.mp4")
    assert code == 0, err
    sent = hosted.calls("POST", "/box/heygen/v3/videos")[0]
    assert sent["headers"]["Authorization"] == f"Bearer {TOKEN}" and sent["headers"]["User-Agent"] == "Ken"
    assert "X-Api-Key" not in sent["headers"]
    assert sent["headers"]["X-Ken-Seconds"] == "3" and sent["headers"]["X-Ken-Consent"] == "yes%20thats%20my%20avatar"
    assert out["account"] == "ken" and out["quota_left"] == "3.5 min" and (tmp_path / "a.mp4").read_bytes() == MP4
    assert all(s["path"].startswith(("/box/heygen", "/files/")) for s in hosted.seen)


def test_on_hosted_ken_check_and_estimate_show_minutes_not_money(hosted, capsys):
    _, out, _ = run(capsys, "check")
    assert out == {"left": {"avatar_video": "4 min"}, "left_seconds": 240, "resets": "2026-12-01", "account": "ken"}
    _, out, _ = run(capsys, "estimate", "--script", "word " * 75)
    assert out["fits"] is True and out["seconds"] == 30 and "usd" not in json.dumps(out)
    _, out, _ = run(capsys, "estimate", "--script", "word " * 700)
    assert out["fits"] is False


def test_allowance_used_up_is_one_line_with_the_own_key_way_out(hosted, capsys, tmp_path):
    hosted.create_error = (402, {"error": "You've used this month's 5 avatar minutes; they come back on Dec 1.", "code": "quota"}, {})
    code, _, err = generate(capsys, tmp_path / "a.mp4")
    assert code == 1 and err.startswith("Allowance used up: You've used this month's 5 avatar minutes")
    assert "HEYGEN_API_KEY" in err and len(hosted.calls("POST")) == 1


def test_a_host_without_the_route_is_one_line(hosted, capsys):
    hosted.host_live = False
    code, _, err = run(capsys, "avatars")
    assert code == 1 and err == h.NOT_LIVE


def test_their_own_key_wins_over_the_host(hosted, capsys, monkeypatch):
    monkeypatch.setenv("HEYGEN_API_KEY", KEY)
    monkeypatch.setenv("HEYGEN_BASE_URL", hosted.url)
    _, out, _ = run(capsys, "check")
    assert out["account"] == "own" and "wins over Ken's allowance" in out["account_note"]
    assert [s["path"] for s in hosted.seen] == ["/v3/users/me"]


def test_the_host_is_derived_from_the_included_ai_url_and_plain_http_is_refused(monkeypatch):
    for name in ("HEYGEN_API_KEY", "KEN_HEYGEN_URL", "KEN_HEYGEN_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://app.example.dev/ai")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", TOKEN)
    assert h.host() == ("https://app.example.dev/box/heygen", TOKEN)
    monkeypatch.setenv("KEN_HEYGEN_URL", "http://evil.example/box/heygen")
    assert h.host() is None


def test_ken_runs_the_helper_itself(fake, tmp_path):
    """`<ken> --heygen ...` end to end, in a clean environment with a temp home."""
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "BROWSER", "TELEGRAM_BOT_TOKEN")
           and not k.startswith(("KEN_", "HEYGEN_"))}
    env.update(KEN_HOME=str(tmp_path / "home"), HEYGEN_API_KEY=KEY, HEYGEN_BASE_URL=fake.url)
    out_file = tmp_path / "avatar.mp4"
    done = subprocess.run([sys.executable, str(ROOT / "bot.py"), "--heygen", "generate", "--avatar", "look_me", "--script", "hello there",
                           "--said", "yes its me", "--own-avatar", "--out", str(out_file), "--every", "0.01"],
                          capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["file"] == str(out_file) and KEY not in done.stdout + done.stderr
    assert out_file.read_bytes() == MP4
