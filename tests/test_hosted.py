"""Ken on its own computer in the cloud, used in a browser (KEN_PLATFORM): the app behind an HTTPS proxy,
connecting an AI from afar, no voice, the plan's included AI and usage, the time zone, and the box's supervisor."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import onboarding  # noqa: E402
import pins  # noqa: E402
from engine import friendly_error  # noqa: E402
from onboarding import BrainOnboarding, CodexOnboarding, Onboarding  # noqa: E402
from web import WebApp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# The Agent37 box is Linux: its shell scripts (and the sign-in helper's) also run on a Mac, not on Windows.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell (bash/sh), as on the Linux box")
TOKEN = "sk-ant-oat01-" + "a" * 40
CLAUDE_KEY = "sk-ant-api03-" + "b" * 40


@pytest.fixture
def engine(tmp_path):
    from test_engine import FakeBrain
    from engine import Engine

    e = Engine(tmp_path, FakeBrain())
    e.configure_home(tmp_path / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    return e


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("KEN_PLATFORM", "test-cloud")


@pytest.fixture
def clean_ai(monkeypatch):
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
              "KEN_INCLUDED_AI_TOKEN", "OPENAI_API_KEY", "KEN_INCLUDED_AI_URL", "KEN_INCLUDED_AI_MODEL"):
        monkeypatch.delenv(k, raising=False)


async def signed_in(engine, aiohttp_client, **kw):
    app = WebApp(engine, engine.home, **kw)
    client = await aiohttp_client(app.build())
    await client.get(f"/login?token={app.token}", allow_redirects=False)
    return app, client


# ------------------------------------------------------------------ behind an HTTPS proxy

async def test_own_page_behind_https_proxy_is_trusted(engine, aiohttp_client):
    """The browser's page is https://host while Ken sees http: its own page must not be refused (blank screen)."""
    app, client = await signed_in(engine, aiohttp_client)
    host = f"{client.server.host}:{client.server.port}"
    assert (await client.get("/api/meta", headers={"Origin": f"https://{host}"})).status == 200
    assert (await client.get("/api/meta", headers={"Origin": f"http://{host}"})).status == 200
    assert (await client.get("/app.js", headers={"Origin": f"https://{host}"})).status == 200


async def test_other_origins_are_still_refused(engine, aiohttp_client):
    app, client = await signed_in(engine, aiohttp_client)
    host = f"{client.server.host}:{client.server.port}"
    for origin in ("https://evil.example", f"https://{host}.evil.example", "null", f"wss://{host}"):
        r = await client.post("/api/chats", json={"name": "x"}, headers={"Origin": origin})
        assert r.status == 403, origin
    r = await client.get("/api/meta", headers={"Origin": "https://evil.example", "X-Forwarded-Proto": "https"})
    assert r.status == 403


async def test_page_on_the_public_name_behind_a_proxy(engine, aiohttp_client, monkeypatch):
    """Agent37's edge hands Ken an internal address as its host: the public name comes from the proxy's
    X-Forwarded-Host, or from KEN_PUBLIC_ORIGINS for the instance's own URLs."""
    app, client = await signed_in(engine, aiohttp_client)
    page = "https://app.example.dev"
    assert (await client.get("/api/meta", headers={"Origin": page})).status == 403
    assert (await client.get("/api/meta", headers={"Origin": page, "X-Forwarded-Host": "app.example.dev"})).status == 200
    assert (await client.get("/api/meta", headers={"Origin": "https://evil.example", "X-Forwarded-Host": "app.example.dev"})).status == 403
    monkeypatch.setenv("KEN_PUBLIC_ORIGINS", "https://ab12cd34ef.agent37.app, https://ab12cd34ef-7777.agent37.app/")
    assert (await client.get("/api/meta", headers={"Origin": "https://ab12cd34ef-7777.agent37.app"})).status == 200
    assert (await client.get("/api/meta", headers={"Origin": "https://zz12cd34ef.agent37.app"})).status == 403


async def test_forwarded_proto_is_honoured(engine, aiohttp_client):
    app, client = await signed_in(engine, aiohttp_client)
    host = f"{client.server.host}:{client.server.port}"
    r = await client.get("/api/meta", headers={"Origin": f"https://{host}", "X-Forwarded-Proto": "https"})
    assert r.status == 200


async def test_page_allows_its_own_secure_socket(engine, aiohttp_client):
    app, client = await signed_in(engine, aiohttp_client)
    csp = (await client.get("/")).headers["Content-Security-Policy"]
    assert f"wss://{client.server.host}:{client.server.port}" in csp
    assert "ws://127.0.0.1:*" in csp


async def test_web_token_from_the_host(engine, aiohttp_client, monkeypatch):
    """The host signs people in itself and forwards with Ken's token, which it chose."""
    monkeypatch.setenv("KEN_WEB_TOKEN", "t" * 32)
    app = WebApp(engine, engine.home)
    assert app.token == "t" * 32 and (engine.home / "web-token").read_text().strip() == "t" * 32
    client = await aiohttp_client(app.build())
    assert (await client.get("/api/meta", headers={"Authorization": "Bearer " + "t" * 32})).status == 200
    assert (await client.get("/api/meta", headers={"Authorization": "Bearer nope"})).status == 401
    monkeypatch.setenv("KEN_WEB_TOKEN", "short")
    with pytest.raises(ValueError):
        WebApp(engine, engine.home)


async def test_meta_says_hosted_and_passes_plan_usage(engine, aiohttp_client, hosted, monkeypatch):
    monkeypatch.setenv("KEN_ACCOUNT_URL", "/account")
    app, client = await signed_in(engine, aiohttp_client)
    usage = {"plan": "Basic", "lines": [{"label": "Awake time", "left": "4 h", "of": "10 h"},
                                        {"label": "AI", "left": "$0.50", "low": True}, {"nope": 1}],
             "manage_url": "javascript:alert(1)"}
    meta = await (await client.get("/api/meta", headers={"X-Ken-Usage": json.dumps(usage)})).json()
    assert meta["hosted"] is True and meta["account_url"] == "/account" and meta["voice"] is False
    assert meta["logout_url"] == "/auth/logout"  # Settings' Sign out: the host's, beside its account page
    assert meta["usage"]["plan"] == "Basic" and len(meta["usage"]["lines"]) == 2
    assert meta["usage"]["lines"][1] == {"label": "AI", "left": "$0.50", "of": "", "low": True}
    assert meta["usage"]["manage_url"] == ""  # only a web or relative link
    for bad in ("not json", "[1, 2]", "{" * 5000):
        assert (await (await client.get("/api/meta", headers={"X-Ken-Usage": bad})).json())["usage"] is None
    assert (await (await client.get("/api/meta")).json())["usage"] is None


async def test_meta_on_a_personal_computer_is_unchanged(engine, aiohttp_client, monkeypatch):
    monkeypatch.delenv("KEN_PLATFORM", raising=False)
    app, client = await signed_in(engine, aiohttp_client)
    meta = await (await client.get("/api/meta")).json()
    assert meta["hosted"] is False and meta["browser"] is False and meta["logout_url"] == ""


def test_sign_out_link_follows_the_account_page(monkeypatch):
    from web import logout_url
    monkeypatch.setenv("KEN_PLATFORM", "agent37")
    monkeypatch.setenv("KEN_ACCOUNT_URL", "https://app.example.dev/account")
    assert logout_url() == "https://app.example.dev/auth/logout"
    monkeypatch.delenv("KEN_ACCOUNT_URL")
    assert logout_url() == ""


async def test_locked_page_points_to_the_website(engine, aiohttp_client, hosted):
    app = WebApp(engine, engine.home)
    client = await aiohttp_client(app.build())
    r = await client.get("/")
    assert r.status == 401 and "ken open" not in await r.text()


# ------------------------------------------------------------------ voice is absent

def test_no_voice_means_nothing_to_get_ready(tmp_path, monkeypatch):
    """Without the transcription model: no "Getting voice ready · 99%" and no huggingface import."""
    monkeypatch.setenv("KEN_VOICE", "0")
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)  # importing it would fail
    o = Onboarding(tmp_path, lambda: True, lambda: None, lambda: False)
    assert o.voice_status() == {"state": "off"}
    assert BrainOnboarding({"claude": o, "codex": o}, lambda: "claude").status()["voice"] == {"state": "off"}


def test_voice_off_when_the_package_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("KEN_VOICE", raising=False)
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "faster_whisper" else real(name, *a))
    assert not onboarding.voice_installed()
    assert Onboarding(tmp_path, lambda: True, lambda: None, lambda: False).voice_status() == {"state": "off"}


async def test_engine_starts_quietly_without_voice(monkeypatch, caplog):
    import bot
    monkeypatch.setenv("KEN_VOICE", "0")
    called = []

    async def warmup_voice():
        called.append(1)
    monkeypatch.setattr(bot, "warmup_voice", warmup_voice)
    await bot.warmup()
    assert not called and not bot.voice_available() and bot.media_mcp() == {}
    assert "Traceback" not in caplog.text


def test_requirements_without_voice():
    text = (ROOT / "requirements.txt").read_text()
    trimmed = pins.without_voice(text)
    names = {pins._name(line) for line in trimmed.splitlines() if line and not line[0].isspace() and not line.startswith("#")}
    assert not names & {"faster-whisper", "av", "ctranslate2", "onnxruntime", "huggingface-hub", "tokenizers"}
    assert {"claude-agent-sdk", "aiohttp", "python-telegram-bot", "numpy", "httpx"} <= names
    sample = "a==1\n    # via -r requirements.in\nb==2\n    # via\n    #   a\n    #   c\nc==3\n    # via a\nd==1\n    # via c\n"
    assert pins.without_voice(sample, {"a"}).strip() == ""
    assert "b==2" in pins.without_voice(sample, {"c"}) and "d==1" not in pins.without_voice(sample, {"c"})


# ------------------------------------------------------------------ connect an AI from afar

async def test_paste_a_claude_token(tmp_path, monkeypatch, clean_ai):
    checked, connected = [], []

    async def check(token="", api_key=""):
        checked.append(token or api_key)
        return "" if token == TOKEN else "Claude didn’t accept that."

    async def on_connected():
        connected.append(1)
    monkeypatch.setattr(onboarding, "check_claude", check)
    o = Onboarding(tmp_path, lambda: bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), on_connected, lambda: True)
    with pytest.raises(ValueError, match="sk-ant-oat"):
        await o.save_token("hello")
    with pytest.raises(ValueError, match="accept"):
        await o.save_token("sk-ant-oat01-" + "z" * 40)
    status = await o.save_token(f"  Your token: {TOKEN}\n")
    assert status["connected"] and status["how"] == "account" and connected
    assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}" in (tmp_path / ".env").read_text()


async def test_claude_api_key_and_disconnect(tmp_path, monkeypatch, clean_ai):
    async def check(token="", api_key=""):
        return ""
    monkeypatch.setattr(onboarding, "check_claude", check)
    o = Onboarding(tmp_path, lambda: bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
                   lambda: _noop(), lambda: True)
    with pytest.raises(ValueError, match="sk-ant-api"):
        await o.save_api_key(TOKEN)
    status = await o.save_api_key(CLAUDE_KEY)
    assert status["connected"] and status["how"] == "key"
    status = await o.disconnect()
    assert not status["connected"] and "ANTHROPIC_API_KEY" not in (tmp_path / ".env").read_text()


async def _noop():
    return None


async def test_setup_api_routes_the_remote_ways_in(engine, aiohttp_client):
    calls = []

    class Setup:
        active = False
        def status(self): return {"connected": True, "providers": {}, "voice": {"state": "off"}, "phone": "declined"}
        def phone_step(self): return "declined"
        async def save_token(self, token): calls.append(("token", token)); return self.status()
        async def save_api_key(self, key, backend=None): calls.append(("key", key, backend)); return self.status()
        async def disconnect(self, backend=None): calls.append(("off", backend)); return self.status()
    app, client = await signed_in(engine, aiohttp_client, setup=Setup())
    assert (await client.post("/api/setup", json={"action": "token", "token": TOKEN})).status == 200
    assert (await client.post("/api/setup", json={"action": "api-key", "key": "k", "backend": "codex"})).status == 200
    assert (await client.post("/api/setup", json={"action": "disconnect", "backend": "claude"})).status == 200
    assert calls == [("token", TOKEN), ("key", "k", "codex"), ("off", "claude")]


async def test_bad_token_is_a_plain_error(engine, aiohttp_client, tmp_path, monkeypatch, clean_ai):
    setup = BrainOnboarding({"claude": Onboarding(tmp_path, lambda: False, _noop, lambda: True),
                             "codex": Onboarding(tmp_path, lambda: False, _noop, lambda: True)}, lambda: "claude")
    app, client = await signed_in(engine, aiohttp_client, setup=setup)
    r = await client.post("/api/setup", json={"action": "token", "token": "nope"})
    assert r.status == 400 and "sk-ant-oat" in (await r.json())["error"]


async def test_chatgpt_signs_in_with_a_code_on_a_computer_in_the_cloud(tmp_path, monkeypatch, hosted, clean_ai):
    import codex_brain

    class Done:
        success, error = True, None

    class Handle:
        verification_url, user_code = "https://auth.openai.com/codex/device", "ABCD-1234"
        async def wait(self): return Done()
        async def cancel(self): pass

    class Client:
        browser = 0
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def account(self):
            return type("A", (), {"account": None, "requires_openai_auth": True})()
        async def login_chatgpt_device_code(self): return Handle()
        async def login_chatgpt(self): Client.browser += 1
        async def close(self): pass
    monkeypatch.setattr(codex_brain, "codex_client", lambda **kw: Client())
    connected = []

    async def on_connected():
        connected.append(1)
    o = CodexOnboarding(tmp_path, on_connected, lambda: True)
    r = await o.start()
    assert r == {"url": Handle.verification_url, "code": "ABCD-1234", "backend": "codex"} and not Client.browser
    await o.watch
    assert o.status()["connected"] and connected


async def test_openai_api_key(tmp_path, monkeypatch, clean_ai):
    import codex_brain

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def account(self):
            return type("A", (), {"account": None, "requires_openai_auth": True})()
    monkeypatch.setattr(codex_brain, "codex_client", lambda **kw: Client())
    o = CodexOnboarding(tmp_path, _noop, lambda: True)
    with pytest.raises(ValueError, match="OpenAI"):
        await o.save_api_key("sk-ant-api03-" + "x" * 30)
    assert (await o.save_api_key("sk-proj-" + "x" * 40))["connected"]
    assert "OPENAI_API_KEY=sk-proj-" in (tmp_path / ".env").read_text()


@posix_only
def test_sign_in_helper_needs_no_browser(tmp_path, monkeypatch):
    """No xdg-open on a computer in the cloud: the helper records the link and succeeds, so the CLI prints it."""
    monkeypatch.setenv("PATH", "/nonexistent")
    script = tmp_path / "signin-browser"
    import asyncio, inspect  # noqa: E401
    source = inspect.getsource(Onboarding.start)
    assert "exit 0" in source
    script.write_text('#!/bin/sh\nprintf %s "$1" > "$0.url"\n'
                      'if command -v open >/dev/null; then exec open "$1"; elif command -v xdg-open >/dev/null; then exec xdg-open "$1"; fi\nexit 0\n')
    script.chmod(0o700)
    r = subprocess.run(["/bin/sh", str(script), "https://claude.com/x"], env={"PATH": "/nonexistent"})
    assert r.returncode == 0 and (tmp_path / "signin-browser.url").read_text() == "https://claude.com/x"


# ------------------------------------------------------------------ the plan's included AI

def included(monkeypatch):
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://host.example/ai/")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "person-token")


def test_included_ai_until_they_connect_their_own(monkeypatch, clean_ai):
    """The host's AI endpoint and this person's token, never a provider key; their own Claude wins."""
    included(monkeypatch)
    assert onboarding.use_included_ai()
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://host.example/ai"
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "person-token"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    assert not onboarding.use_included_ai()
    assert "ANTHROPIC_AUTH_TOKEN" not in os.environ and "ANTHROPIC_BASE_URL" not in os.environ
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    assert onboarding.use_included_ai()


def test_without_included_ai_nothing_changes(monkeypatch, clean_ai):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "someone-elses")
    assert not onboarding.use_included_ai()
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "someone-elses"


def test_included_ai_needs_https(monkeypatch, clean_ai):
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "http://host.example/ai")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "person-token")
    assert not onboarding.use_included_ai() and "ANTHROPIC_AUTH_TOKEN" not in os.environ


def test_included_ai_model_is_the_plans(monkeypatch, clean_ai, tmp_path):
    import bot
    included(monkeypatch)
    monkeypatch.setenv("KEN_INCLUDED_AI_MODEL", "claude-sonnet-test")
    onboarding.use_included_ai()
    assert bot.active_model("claude") == "claude-sonnet-test"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    onboarding.use_included_ai()
    assert bot.active_model("claude") != "claude-sonnet-test"


def test_status_says_included(tmp_path, monkeypatch, clean_ai):
    included(monkeypatch)
    o = Onboarding(tmp_path, lambda: True, _noop, lambda: True)
    assert o.status()["how"] == "included"


def test_included_ai_running_out_is_said_plainly(monkeypatch, clean_ai):
    included(monkeypatch)
    onboarding.use_included_ai()
    msg = friendly_error(Exception('API Error: 402 {"type":"error","error":{"type":"billing_error","message":"Out of included AI"}}'), "claude")
    assert msg == "You've used your Ken AI credits."
    monkeypatch.delenv("KEN_INCLUDED_AI_TOKEN")
    assert "included" not in friendly_error(Exception("API Error: 402 Out of included AI"), "claude")


# ------------------------------------------------------------------ bot: hosted bits

def test_next_job_time(monkeypatch):
    import bot
    brief = {"name": "morning-brief", "time": "07:00", "days": "daily"}
    now = time.mktime(time.strptime("2026-10-05 05:30", "%Y-%m-%d %H:%M"))
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(bot.next_job_time([brief], {}, now))) == "2026-10-05 07:00"
    ran = {"morning-brief": "2026-10-05 07:00"}
    later = time.mktime(time.strptime("2026-10-05 08:00", "%Y-%m-%d %H:%M"))
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(bot.next_job_time([brief], ran, later))) == "2026-10-06 07:00"
    assert bot.next_job_time([brief], {}, later) == later  # missed while asleep: due now
    assert bot.next_job_time([{**brief, "on": False}], {}, now) is None
    assert bot.next_job_time([], {}, now) is None


def test_timezone_command(monkeypatch, tmp_path):
    import bot
    monkeypatch.setattr(bot, "KEN_HOME", tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    try:
        r = bot.set_timezone("Europe/Lisbon")
        assert r["timezone"] == "Europe/Lisbon" and os.environ["TZ"] == "Europe/Lisbon"
        assert "TZ=Europe/Lisbon" in (tmp_path / ".env").read_text()
        for bad in ("Mars/Olympus", "../../etc/passwd", "Europe/Lisbon; rm -rf ~"):
            assert bot.set_timezone(bad)["timezone"] == "Europe/Lisbon"
    finally:
        monkeypatch.setenv("TZ", "UTC")
        if hasattr(time, "tzset"):
            time.tzset()


def test_no_coffee_in_the_cloud(monkeypatch, hosted):
    import bot
    assert bot.COMMANDS["awake"]("") == {"awake": None}


def test_browser_only_when_the_plan_has_one(monkeypatch, hosted, tmp_path):
    import bot
    chromium = tmp_path / "chromium"
    chromium.write_text("")
    monkeypatch.setenv("KEN_CHROMIUM", str(chromium))
    monkeypatch.delenv("KEN_BROWSER", raising=False)
    assert bot.browser_command() == {}
    monkeypatch.setenv("KEN_BROWSER", "1")
    script = tmp_path / "mcp.js"
    script.write_text("")
    monkeypatch.setenv("KEN_NODE", "/usr/bin/node")
    monkeypatch.setenv("KEN_BROWSER_MCP", str(script))
    args = bot.browser_command()["browser"]["args"]
    assert "--headless" in args and f"--executablePath={chromium}" in args and "--autoConnect" not in args


def test_apps_server_from_the_host(monkeypatch):
    import bot
    monkeypatch.delenv("KEN_APPS_MCP_URL", raising=False)
    assert bot.apps_mcp() == {}
    monkeypatch.setenv("KEN_APPS_MCP_URL", "https://apps.example/mcp")
    monkeypatch.setenv("KEN_APPS_MCP_TOKEN", "tok")
    assert bot.apps_mcp() == {"apps": {"type": "http", "url": "https://apps.example/mcp", "headers": {"Authorization": "Bearer tok"}}}
    from codex_brain import mcp_config
    assert mcp_config(bot.apps_mcp())["apps"]["http_headers"] == {"Authorization": "Bearer tok"}


def test_hosted_prompt_is_for_the_browser(monkeypatch, hosted):
    import bot
    system = bot.build_system()
    assert "IN THE CLOUD" in system and "only through Telegram" not in system


def test_phone_step_skipped_in_the_cloud(tmp_path, monkeypatch, hosted):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert Onboarding(tmp_path, lambda: True, _noop, lambda: True).phone_step() == "declined"


async def test_keep_awake_checks_in(monkeypatch):
    import bot
    import httpx
    posts = []

    def handler(request):
        posts.append((request.headers["authorization"], json.loads(request.content)))
        raise SystemExit  # one check-in is enough
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "https://host.example/box/checkin")
    monkeypatch.setenv("KEN_KEEPALIVE_TOKEN", "secret")
    with pytest.raises(SystemExit):
        await bot.keep_awake()
    assert posts[0][0] == "Bearer secret" and set(posts[0][1]) == {"busy", "next_job", "rev", "started", "talked"}
    assert posts[0][1]["started"] == bot.STARTED_AT and posts[0][1]["talked"] == bot.BORN_FLAG.exists()
    # How Ken thinks, once chosen on its first screen (the host's funnel counts it once).
    class Claude:
        def ai_choice(self):
            return "credits"
    monkeypatch.setattr(bot, "ACCOUNTS", type("A", (), {"providers": {"claude": Claude()}, "active": False})())
    with pytest.raises(SystemExit):
        await bot.keep_awake()
    assert posts[1][1]["ai"] == "credits"
    monkeypatch.setenv("KEN_KEEPALIVE_URL", "http://insecure.example")
    assert await bot.keep_awake() is None


# ------------------------------------------------------------------ the box's supervisor

def test_box_image_settings():
    dockerfile = (ROOT / "deploy" / "agent37" / "Dockerfile").read_text()
    assert "KEN_WEB_HOST=0.0.0.0" in dockerfile and "KEN_WEB_PORT=7777" in dockerfile and "KEN_VOICE=0" in dockerfile
    assert "pins.py" in dockerfile  # the image leaves voice out


@posix_only
def test_box_scripts_parse():
    for name in ("ken-start", "ken-supervise"):
        assert subprocess.run(["bash", "-n", str(ROOT / "deploy" / "agent37" / name)]).returncode == 0


@posix_only
def test_supervisor_sets_up_restarts_and_updates(tmp_path):
    """A fake Ken that exits twice (like an update) is started again each time, after a git pull."""
    seed, venv, home = tmp_path / "seed", tmp_path / "venv", tmp_path / "home"
    seed.mkdir()
    subprocess.run(["git", "init", "-q", str(seed)], check=True)
    for name in ("SOUL.template.md", "jobs.default.json", "ken", "requirements.txt"):
        (seed / name).write_text("hi {{NAME}}\n" if name.startswith("SOUL") else "[]\n" if name.endswith("json") else "#!/bin/sh\n" if name == "ken" else "")
    (seed / "pins.py").write_text((ROOT / "pins.py").read_text())
    runs = tmp_path / "runs"
    (seed / "bot.py").write_text(f"import pathlib, sys\np = pathlib.Path({str(runs)!r})\nn = int(p.read_text()) if p.exists() else 0\n"
                                 "p.write_text(str(n + 1))\nsys.exit(0 if n < 2 else 99)\n")
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    (venv / "bin" / "pip").write_text("#!/bin/sh\necho pip \"$@\" >> \"$HOME/pip.log\"\n")
    (venv / "bin" / "pip").chmod(0o755)
    script = (ROOT / "deploy" / "agent37" / "ken-supervise").read_text()
    script = script.replace("SEED=/opt/ken/app-seed", f"SEED={seed}").replace("VENV=/opt/ken/venv", f"VENV={venv}")
    script = script.replace('sleep "$wait"', '[ "$(cat ' + str(runs) + ')" -ge 3 ] && exit 0; sleep 0')
    (tmp_path / "sup").write_text(script)
    home.mkdir()
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "KEN_OWNER_NAME": "Sam"}
    r = subprocess.run(["bash", str(tmp_path / "sup")], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert runs.read_text() == "3"
    ken = home / ".ken"
    assert (ken / "app" / "bot.py").exists() and os.readlink(ken / "venv") == str(venv)
    assert (ken / "work" / "SOUL.md").read_text() == "hi Sam\n" and (ken / "jobs.json").exists()
    assert "Ken's own computer in the cloud" in (ken / "hosting.md").read_text()
    assert oct((ken / ".env").stat().st_mode)[-3:] == "600"
    assert (home / "pip.log").read_text().count("install -q -r") == 3
    assert r.stdout.count("starting Ken") == 3


@posix_only
def test_update_gets_through_a_code_folder_that_changed_here(tmp_path):
    """A plain pull can't move a code folder with a local commit and an edit; the update resets it to its branch."""
    git = lambda *a, cwd=None: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
    origin, app, venv = tmp_path / "origin", tmp_path / "home" / ".ken" / "app", tmp_path / "venv"
    git("init", "-q", "-b", "hosted-agent37", str(origin))
    (origin / "bot.py").write_text("v1\n")
    (origin / "requirements.txt").write_text("")
    (origin / "pins.py").write_text((ROOT / "pins.py").read_text())
    git("add", ".", cwd=origin); git("commit", "-qm", "v1", cwd=origin)
    git("clone", "-q", "-b", "hosted-agent37", str(origin), str(app))
    # Here: an edit and a local commit. There: the next version of Ken.
    (app / "bot.py").write_text("changed here\n"); git("commit", "-qam", "local", cwd=app)
    (app / "pins.py").write_text("# edited\n" + (app / "pins.py").read_text())
    (origin / "bot.py").write_text("v2\n"); git("commit", "-qam", "v2", cwd=origin)
    (tmp_path / "home" / ".ken" / "memory").mkdir()
    (tmp_path / "home" / ".ken" / "memory" / "people.md").write_text("theirs\n")
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    (venv / "bin" / "pip").write_text("#!/bin/sh\nexit 0\n")
    (venv / "bin" / "pip").chmod(0o755)
    script = ROOT / "deploy" / "agent37" / "ken-supervise"
    env = {"HOME": str(tmp_path / "home"), "PATH": os.environ["PATH"]}
    r = subprocess.run(["bash", "-c", f'source "{script}"; VENV={venv}; update'], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (app / "bot.py").read_text() == "v2\n"
    assert git("status", "--porcelain", cwd=app) == ""
    assert git("rev-parse", "HEAD", cwd=app) == git("rev-parse", "HEAD", cwd=origin)
    assert "reset it to origin/hosted-agent37" in r.stdout
    assert (tmp_path / "home" / ".ken" / "memory" / "people.md").read_text() == "theirs\n"  # their data untouched


# ------------------------------------------------------------------ the model menu follows what Ken runs on

class _Accounts:
    def __init__(self, claude=False, codex=False, included=False):
        self.p = {"claude": {"connected": claude or included, "how": "included" if included else "account" if claude else ""},
                  "codex": {"connected": codex}}

    def status(self):
        return {"providers": self.p}


async def test_on_ken_ai_credits_the_menu_is_sonnet_only(monkeypatch, clean_ai, tmp_path):
    import bot
    included(monkeypatch)
    monkeypatch.setenv("KEN_INCLUDED_AI_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(bot, "ACCOUNTS", _Accounts(included=True))
    monkeypatch.setattr(bot, "KEN_HOME", tmp_path)
    (tmp_path / "model").write_text("claude-opus-5-5\n")  # chosen before, on their own Claude
    assert onboarding.use_included_ai()
    assert await bot.model_choices("claude") == ["claude-sonnet-5-5"]
    assert await bot.model_choices("codex") == []  # ChatGPT isn't connected: none of its models
    assert bot.usable_brains() == ["claude"]
    assert bot.active_model("claude") == "claude-sonnet-5-5"  # the saved Opus quietly gives way
    reply = await bot.model("opus")
    assert reply["reply"].startswith("Ken AI credits run on Sonnet.") and reply["choices"] == ["claude-sonnet-5-5"]
    assert (tmp_path / "model").read_text() == "claude-opus-5-5\n"  # kept for when their own Claude is back


async def test_their_own_accounts_list_their_own_models(monkeypatch, clean_ai, tmp_path):
    import bot
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "own")
    monkeypatch.setattr(bot, "ACCOUNTS", _Accounts(claude=True))
    monkeypatch.setattr(bot, "KEN_HOME", tmp_path)
    monkeypatch.setattr(bot, "models_file", lambda backend: tmp_path / f"models-{backend}")
    (tmp_path / "models-claude").write_text("claude-opus-5-5\nclaude-sonnet-5-5\n")

    async def refresh(backend=None):
        pass
    monkeypatch.setattr(bot, "refresh_models_file", refresh)
    assert bot.credits_model("claude") == ""
    assert await bot.model_choices("claude") == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert await bot.model_choices("codex") == []
    monkeypatch.setattr(bot, "ACCOUNTS", _Accounts(claude=True, codex=True))
    (tmp_path / "models-codex").write_text("gpt-luna\n")
    assert await bot.model_choices("codex") == ["gpt-luna"]
    assert bot.usable_brains() == ["claude", "codex"]


async def test_the_runtime_says_credits_and_only_usable_brains(engine):
    from engine import HOME
    first = next(iter(engine.brains.values()))
    engine.brains.update({"claude": first, "codex": first})
    engine.kv_set(f"backend:{HOME}", "claude")
    engine.usable_brains = lambda: ["claude"]
    engine.credits_model = lambda backend: "claude-sonnet-5-5" if backend == "claude" else ""

    async def choices(backend):
        return ["claude-sonnet-5-5"] if backend == "claude" else []
    engine.model_choices = choices
    r = await engine.runtime(HOME)
    assert r == {"backend": "claude", "current": "claude-sonnet-5-5", "choices": ["claude-sonnet-5-5"], "providers": ["claude"], "credits": True}
    assert engine.model_for(HOME) == "claude-sonnet-5-5"
