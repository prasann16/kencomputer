"""Captchas: off unless KEN_CAPTCHA=1; then a free vision model reads a text captcha and types it in, a checkbox gets
one click, a paid solver (CapSolver) is tried only when its key is set, and anything left is handed to the person
with a screenshot. The browser here is a fake that answers each page script the way a real page would."""
import base64
import json
import os
import tempfile

import pytest

os.environ["KEN_HOME"] = tempfile.mkdtemp()  # never a live Ken (see conftest.py)
import bot  # noqa: E402
import captcha  # noqa: E402

PNG = base64.b64encode(b"\x89PNG fake").decode()
TEXT_CAPTCHA = {"kind": "image", "site": "2captcha.com", "url": "https://2captcha.com/demo/normal", "box": [10, 10, 250, 50],
                "has_input": True, "captcha_only_form": True}
RECAPTCHA = {"kind": "recaptcha", "site": "example.com", "url": "https://example.com/form", "sitekey": "6Le-key",
             "enterprise": False, "data_s": "", "callback": "onDone"}


class FakePage:
    def __init__(self, found, after=None, token="", challenge=False):
        self.found, self.after, self.token, self.challenge = found, after, token, challenge
        self.calls, self.typed, self.clicked = [], [], []

    async def js(self, page_id, fn):
        self.calls.append(fn)
        if fn == captcha.DETECT_JS:
            return self.found
        if fn == captcha.IMAGE_JS:
            return "data:image/png;base64," + PNG
        if fn == captcha.AFTER_JS:
            return self.after
        if fn == captcha.TOKEN_JS:
            return {"token": self.token, "challenge": self.challenge}
        self.typed.append(fn)
        return {"typed": True, "submitted": "if (!true)" in fn} if "data-ken-captcha=input" in fn else {"set": True}

    async def screenshot(self, page_id):
        return PNG

    async def snapshot(self, page_id):
        return 'uid=8_12 IframePresentational "reCAPTCHA"\nuid=8_13 button "Submit"'

    async def click(self, page_id, uid):
        self.clicked.append(uid)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("KEN_CAPTCHA", "KEN_CAPTCHA_PROVIDER", "KEN_CAPTCHA_API_KEY", "KEN_CAPTCHA_VISION_MODEL", "ANTHROPIC_API_KEY",
              "KEN_INCLUDED_AI_URL", "KEN_INCLUDED_AI_TOKEN", "KEN_INCLUDED_AI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(captcha.asyncio, "sleep", _no_wait)


async def _no_wait(_):
    return None


def test_off_by_default(monkeypatch):
    monkeypatch.setitem(bot.SHARED_MCP, "browser", {"type": "http", "url": "http://127.0.0.1:7777/mcp/browser"})
    assert bot.captcha_mcp() == {}
    monkeypatch.setattr(bot, "where_facts", lambda: {"browser": "own"})
    assert "solve_captcha" not in bot.browser_prompt()
    monkeypatch.setenv("KEN_CAPTCHA", "1")
    assert set(bot.captcha_mcp()) == {"captcha"}
    assert "solve_captcha" in bot.browser_prompt()
    codex = bot.captcha_mcp("codex")["captcha"]
    assert "captcha" in codex["args"] and json.loads(codex["env"]["KEN_CAPTCHA_BROWSER"])["url"].endswith("/mcp/browser")


def test_needs_the_shared_browser(monkeypatch):
    monkeypatch.setenv("KEN_CAPTCHA", "1")
    monkeypatch.delitem(bot.SHARED_MCP, "browser", raising=False)
    assert bot.captcha_mcp() == {}


def test_only_the_paid_solver_with_its_key(monkeypatch):
    assert [s.name for s in captcha.solvers()] == ["vision"]
    monkeypatch.setenv("KEN_CAPTCHA_PROVIDER", "capsolver")
    assert [s.name for s in captcha.solvers()] == ["vision"]  # no key, no paid solver
    monkeypatch.setenv("KEN_CAPTCHA_API_KEY", "CAP-test")
    assert [s.name for s in captcha.solvers()] == ["vision", "capsolver"]


def test_vision_uses_own_key_then_included_ai(monkeypatch):
    assert captcha.vision_api() is None
    monkeypatch.setenv("KEN_INCLUDED_AI_URL", "https://host.example/ai")
    monkeypatch.setenv("KEN_INCLUDED_AI_TOKEN", "tok")
    monkeypatch.setenv("KEN_INCLUDED_AI_MODEL", "claude-haiku-5-5")
    assert captcha.vision_api() == ("https://host.example/ai", {"authorization": "Bearer tok"}, "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-x")
    assert captcha.vision_api()[1] == {"x-api-key": "sk-ant-api-x"}


async def test_no_captcha():
    out = await captcha.solve(FakePage(None), 1)
    assert out.status == "none"


async def test_vision_reads_and_types_a_text_captcha(monkeypatch):
    asked = []
    monkeypatch.setattr(captcha, "ask_vision", lambda api, image, note="": asked.append(image) or "W9H5K")
    page = FakePage(TEXT_CAPTCHA, after={"still": True, "url": TEXT_CAPTCHA["url"], "text": "Captcha is passed successfully!"})
    out = await captcha.solve(page, 1, using=[captcha.Vision(api=lambda: ("https://x", {}, "m"))])
    assert asked == [PNG]
    assert out.status == "submitted" and out.by == "vision" and "passed successfully" in out.detail
    assert '"W9H5K"' in page.typed[0] and "if (!true)" in page.typed[0]  # a captcha-only form is submitted


async def test_a_form_with_other_fields_is_left_for_ken_to_submit(monkeypatch):
    monkeypatch.setattr(captcha, "ask_vision", lambda *a, **k: "abc12")
    page = FakePage({**TEXT_CAPTCHA, "captcha_only_form": False})
    out = await captcha.solve(page, 1, using=[captcha.Vision(api=lambda: ("https://x", {}, "m"))])
    assert out.status == "submitted" and "submit it yourself" in out.detail and "if (!false)" in page.typed[0]


async def test_without_a_vision_model_ken_reads_it_himself():
    out = await captcha.solve(FakePage(TEXT_CAPTCHA), 3, using=[captcha.Vision(api=lambda: None)])
    assert out.status == "read_it" and out.image == PNG and "page_id=3" in out.detail
    page = FakePage(TEXT_CAPTCHA, after={"still": False, "url": TEXT_CAPTCHA["url"], "text": ""})
    out = await captcha.solve(page, 3, answer="W9H5K")
    assert out.status == "solved" and '"W9H5K"' in page.typed[0]


async def test_checkbox_click_that_passes():
    page = FakePage(RECAPTCHA, token="03AF-token")
    out = await captcha.solve(page, 1, using=[captcha.Vision(api=lambda: None)])
    assert out.status == "solved" and page.clicked == ["8_12"]


async def test_puzzle_goes_to_the_paid_solver_then_handoff(monkeypatch, tmp_path):
    page = FakePage(RECAPTCHA, challenge=True)
    paid = captcha.CapSolver("CAP-test", wait=1, every=0)
    sent = []

    def post(path, body):
        sent.append((path, body))
        return {"errorId": 0, "taskId": "t1"} if path == "/createTask" else {"errorId": 0, "status": "ready",
                                                                             "solution": {"gRecaptchaResponse": "03AF-paid"}}
    monkeypatch.setattr(paid, "post", post)
    out = await captcha.solve(page, 1, using=[captcha.Vision(api=lambda: None), paid])
    assert out.status == "solved" and out.by == "capsolver"
    assert sent[0][1]["task"] == {"type": "ReCaptchaV2TaskProxyLess", "websiteURL": RECAPTCHA["url"], "websiteKey": "6Le-key"}
    assert '"03AF-paid"' in page.typed[-1] and '"onDone"' in page.typed[-1]

    # The paid solver failing too: the person is told, with a screenshot saved for them.
    monkeypatch.setattr(paid, "post", lambda path, body: {"errorId": 1, "errorDescription": "ERROR_KEY_DENIED_ACCESS"})
    out = await captcha.solve(FakePage(RECAPTCHA, challenge=True), 1, using=[captcha.Vision(api=lambda: None), paid],
                              save_dir=tmp_path)
    assert out.status == "handoff" and "picture puzzle" in out.detail and "ERROR_KEY_DENIED_ACCESS" in out.detail
    assert out.saved and open(out.saved, "rb").read() == base64.b64decode(PNG) and out.saved in out.detail


def test_capsolver_tasks():
    cap = captcha.Captcha.from_page
    assert captcha.CapSolver.task(cap({**RECAPTCHA, "data_s": "S"}))["recaptchaDataSValue"] == "S"
    assert captcha.CapSolver.task(cap({**RECAPTCHA, "enterprise": True}))["type"] == "ReCaptchaV2EnterpriseTaskProxyLess"
    assert captcha.CapSolver.task(cap({"kind": "turnstile", "url": "u", "sitekey": "0x4", "action": "login"})) == {
        "type": "AntiTurnstileTaskProxyLess", "websiteURL": "u", "websiteKey": "0x4", "metadata": {"action": "login"}}
    assert captcha.CapSolver.task(cap(TEXT_CAPTCHA), PNG) == {"type": "ImageToTextTask", "body": PNG}
    assert not captcha.CapSolver("k").handles(cap({"kind": "hcaptcha"}))


async def test_cloudflare_check_is_handed_off():
    out = await captcha.solve(FakePage({"kind": "cloudflare", "site": "x.com", "url": "https://x.com"}), 1,
                              using=captcha.solvers())
    assert out.status == "handoff" and "IP" in out.detail and out.image == PNG


def test_vision_request(monkeypatch):
    seen = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '"W9H5K"\n'}]}).encode()

    def urlopen(req, timeout):
        seen.update(url=req.full_url, body=json.loads(req.data), auth=req.get_header("Authorization"))
        return Reply()

    monkeypatch.setattr(captcha.urllib.request, "urlopen", urlopen)
    assert captcha.ask_vision(("https://host/ai", {"authorization": "Bearer t"}, "m"), PNG) == "W9H5K"
    assert seen["url"] == "https://host/ai/v1/messages" and seen["auth"] == "Bearer t"
    assert seen["body"]["thinking"] == {"type": "disabled"} and seen["body"]["messages"][0]["content"][0]["source"]["data"] == PNG
