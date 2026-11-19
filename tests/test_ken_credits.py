"""`ken --credits` (ken_credits.py): what used their Ken AI credits this month, from the host's list."""
import io
import json

import ken_credits

USAGE = {"period": "2026-11", "total": "$0.62", "days": [
    {"day": "2026-11-19", "label": "Today", "total": "$0.58", "entries": [
        {"kind": "video", "label": "Dub · track club reel (Portuguese)", "usd": "$0.55", "uses": 1},
        {"kind": "ai", "label": "AI replies (Haiku)", "usd": "$0.03", "uses": 41}]},
    {"day": "2026-11-18", "label": "Yesterday", "total": "$0.04", "entries": [
        {"kind": "ai", "label": "AI replies (Haiku)", "usd": "$0.04", "uses": 52}]},
]}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_list_reads_as_plain_lines():
    assert ken_credits.report(USAGE).splitlines() == [
        "Ken AI credits used this month: $0.62",
        "Today · Dub · track club reel (Portuguese) · $0.55",
        "Today · AI replies (Haiku) · $0.03",
        "Yesterday · AI replies (Haiku) · $0.04",
    ]
    assert ken_credits.report({"days": []}) == "Nothing has used Ken AI credits this month yet."


def test_it_asks_the_host_with_this_computers_token(monkeypatch, capsys):
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://app.example.dev/ai")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "tok123")
    seen = []

    def urlopen(req, timeout=None):
        seen.append((req.full_url, req.headers.get("Authorization")))
        return Resp(json.dumps(USAGE).encode())
    monkeypatch.setattr(ken_credits.urllib.request, "urlopen", urlopen)
    assert ken_credits.main() == 0
    assert seen == [("https://app.example.dev/box/credits", "Bearer tok123")]
    assert "Dub · track club reel (Portuguese) · $0.55" in capsys.readouterr().out


def test_without_hosted_ken_it_says_so(monkeypatch, capsys):
    monkeypatch.delenv("KEN_INCLUDED_AI_URL", raising=False)
    monkeypatch.delenv("KEN_INCLUDED_AI_TOKEN", raising=False)
    assert ken_credits.main() == 1 and "only on hosted Ken" in capsys.readouterr().out
