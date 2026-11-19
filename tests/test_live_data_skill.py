"""The live-data skill's helper (skills/live-data/scripts/tools.py): through the host's /box/tools only."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("live_tools", ROOT / "skills" / "live-data" / "scripts" / "tools.py")
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


def test_skill_names_the_helper_and_is_readable():
    text = (ROOT / "skills" / "live-data" / "SKILL.md").read_text()
    assert text.startswith("---\nname: live-data\n")
    assert "--tools search" in text and "--max" in text


def test_without_a_host_nothing_runs(monkeypatch, capsys):
    for k in ("KEN_TOOLS_URL", "KEN_INCLUDED_AI_URL", "KEN_INCLUDED_AI_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert tools.main(["search", "reels"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "not_hosted"


def test_host_is_the_sites_box_tools_from_the_ai_url(monkeypatch):
    monkeypatch.delenv("KEN_TOOLS_URL", raising=False)
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://kencomputer.dev/ai")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "tok")
    assert tools.host() == ("https://kencomputer.dev/box/tools", "tok")


class _Host(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Host.seen.append((self.path, self.headers.get("Authorization"), self.headers.get("User-Agent"), body))
        out = json.dumps({"reels": [{"views": 10}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("x-ken-charged", "0.0022")
        self.send_header("x-ken-credits-left", "3.97")
        self.send_header("x-ken-call-id", "call_1")
        self.end_headers()
        self.wfile.write(out)


def test_a_call_sends_the_ceiling_and_prints_what_it_cost(monkeypatch, capsys):
    server = HTTPServer(("127.0.0.1", 0), _Host)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("KEN_TOOLS_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "tok")
        assert tools.main(["call", "treg.instagram.user.reels", "--max", "0.01", "--data", '{"username": "a"}']) == 0
        first, rest = capsys.readouterr().out.split("\n", 1)
        assert json.loads(first) == {"charged_usd": 0.0022, "credits_left_usd": "3.97", "call_id": "call_1"}
        assert json.loads(rest) == {"reels": [{"views": 10}]}
        path, auth, agent, body = _Host.seen[-1]
        assert path == "/call/treg.instagram.user.reels" and auth == "Bearer tok" and agent == "Ken"
        assert body == {"max_usd": 0.01, "body": {"username": "a"}}
    finally:
        server.shutdown()
