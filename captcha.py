"""Captchas in Ken's browser: notice one, try to get past it, and say so plainly when it can't.

Off unless KEN_CAPTCHA=1. Then Ken gets one tool, `solve_captcha(page_id)`, which works on a page in the shared
browser (the same Chrome DevTools MCP server Ken browses with) and tries, in order:

  1. vision (free, built in): a text-in-a-picture captcha is cut out of the page and read by a vision model, the
     person's own Anthropic key or the plan's included AI, and the answer typed in. A reCAPTCHA, hCaptcha or
     Turnstile checkbox gets one click, which is often all it takes from a trusted browser and IP.
     With no vision model here, the picture goes back to Ken to read (its own model sees images).
  2. a paid solver, when KEN_CAPTCHA_PROVIDER and KEN_CAPTCHA_API_KEY are set (CapSolver for now): it returns a
     token for reCAPTCHA v2 / Turnstile, or the text of an image captcha.
  3. handoff: a screenshot saved to the workspace and plain words for Ken to tell the person.

What this can't do, honestly: picture grids ("select all buses") are left to the paid solver or the person.
reCAPTCHA v3, Google's /sorry/ page and Cloudflare's "Just a moment" are scored on how much the site trusts the
browser and its IP; from a datacenter IP the score is low whatever is solved, so those need a residential IP.
That's why the handoff is always there.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_VISION_MODEL = "claude-haiku-5-5"
WIDGETS = ("recaptcha", "hcaptcha", "turnstile")


def enabled() -> bool:
    return os.environ.get("KEN_CAPTCHA") == "1"


# ------------------------------------------------------------------ the page

class BrowserError(RuntimeError):
    pass


class Browser:
    """Calls tools on the shared browser MCP server (the app server's /mcp/browser), as a chat would."""

    def __init__(self, server: dict):
        self.url, self.headers = server["url"], server.get("headers", {})

    async def call(self, name: str, args: dict) -> list[dict]:
        import aiohttp

        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        async with aiohttp.ClientSession() as s, s.post(self.url, json=msg, headers=self.headers,
                                                         timeout=aiohttp.ClientTimeout(total=120)) as r:
            reply = await r.json()
        if "error" in reply:
            raise BrowserError(reply["error"].get("message", "The browser tool failed."))
        result = reply.get("result", {})
        content = result.get("content", [])
        if result.get("isError"):
            raise BrowserError(" ".join(c.get("text", "") for c in content) or "The browser tool failed.")
        return content

    async def js(self, page_id: int, fn: str):
        """Run a JavaScript function in the page and return what it returned."""
        text = "".join(c.get("text", "") for c in await self.call("evaluate_script", {"pageId": page_id, "function": fn}))
        m = re.search(r"```json\n(.*)\n```", text, re.S)
        if not m:
            raise BrowserError(text.strip() or "The page didn't answer.")
        return json.loads(m.group(1)) if m.group(1) != "undefined" else None

    async def screenshot(self, page_id: int) -> str:
        """The visible part of the page, as base64 PNG."""
        for c in await self.call("take_screenshot", {"pageId": page_id}):
            if c.get("type") == "image":
                return c["data"]
        raise BrowserError("No screenshot came back.")

    async def snapshot(self, page_id: int) -> str:
        return "".join(c.get("text", "") for c in await self.call("take_snapshot", {"pageId": page_id}))

    async def click(self, page_id: int, uid: str) -> None:
        await self.call("click", {"pageId": page_id, "uid": uid})


# Finds a captcha on the page and tags its parts (data-ken-captcha=img|input|submit) so later steps find them.
DETECT_JS = r"""() => {
  const shown = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  document.querySelectorAll('[data-ken-captcha]').forEach(e => e.removeAttribute('data-ken-captcha'));
  const out = {url: location.href, site: location.hostname, title: document.title};
  const frames = [...document.querySelectorAll('iframe')];
  const param = (f, k) => { try { const u = new URL(f.src, location.href);
    return u.searchParams.get(k) || new URLSearchParams(u.hash.slice(1)).get(k) || ''; } catch (e) { return ''; } };
  const rc = frames.find(f => /\/recaptcha\/(api2|enterprise)\/anchor/.test(f.src) && shown(f));
  if (rc) { const d = document.querySelector('.g-recaptcha, [data-sitekey]');
    return {...out, kind: 'recaptcha', sitekey: (d && d.getAttribute('data-sitekey')) || param(rc, 'k'),
      enterprise: /\/enterprise\//.test(rc.src), data_s: (d && d.getAttribute('data-s')) || '',
      callback: (d && d.getAttribute('data-callback')) || ''}; }
  const hc = frames.find(f => /hcaptcha\.com/.test(f.src) && shown(f));
  if (hc) { const d = document.querySelector('.h-captcha, [data-hcaptcha-widget-id]');
    return {...out, kind: 'hcaptcha', sitekey: (d && d.getAttribute('data-sitekey')) || param(hc, 'sitekey')}; }
  const ts = document.querySelector('.cf-turnstile, [name="cf-turnstile-response"]');
  if (ts) { const d = document.querySelector('.cf-turnstile[data-sitekey]');
    return {...out, kind: 'turnstile', sitekey: (d && d.getAttribute('data-sitekey')) || '',
      action: (d && d.getAttribute('data-action')) || '', callback: (d && d.getAttribute('data-callback')) || ''}; }
  if (/^Just a moment/i.test(document.title) || document.querySelector('#challenge-form, #cf-challenge-running'))
    return {...out, kind: 'cloudflare'};
  const words = /captcha|securimage|verif|security.?code/i;
  const img = [...document.querySelectorAll('img, canvas')].find(e => {
    const r = e.getBoundingClientRect(), src = e.currentSrc || e.src || '';
    const about = [e.id, e.className, e.getAttribute('alt'), src, e.getAttribute('name'), e.getAttribute('title')].join(' ');
    return shown(e) && r.width >= 50 && r.width <= 500 && r.height >= 20 && r.height <= 200
      && !/\.svg(\?|#|$)/i.test(src) && !/logo/i.test(about) && words.test(about); });
  if (!img) return null;
  let box = img.parentElement, input = null;
  const typable = 'input:not([type]), input[type=text], input[type=number], input[type=tel], textarea';
  for (let i = 0; box && i < 6 && !input; i++, box = box.parentElement) {
    const found = [...box.querySelectorAll(typable)].filter(shown);
    input = found.find(e => words.test([e.id, e.name, e.placeholder, e.getAttribute('aria-label')].join(' ')))
      || found.find(e => /code|answer|text|image|characters/i.test([e.id, e.name, e.placeholder, e.getAttribute('aria-label')].join(' ')))
      || (found.length === 1 ? found[0] : null);
  }
  img.setAttribute('data-ken-captcha', 'img');
  const r = img.getBoundingClientRect();
  const res = {...out, kind: 'image', box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    has_input: !!input, captcha_only_form: false};
  if (input) {
    input.setAttribute('data-ken-captcha', 'input');
    const form = input.form || input.closest('form');
    if (form) {
      const fields = [...form.querySelectorAll('input, textarea, select')].filter(e => shown(e) && !/submit|button|hidden|reset/i.test(e.type));
      res.captcha_only_form = fields.length === 1;
      const btn = [...form.querySelectorAll('button, input[type=submit]')].find(b => shown(b) && (b.type === 'submit' || /submit|check|verify|continue|send/i.test(b.innerText || b.value)));
      if (btn) btn.setAttribute('data-ken-captcha', 'submit');
    }
  }
  return res;
}"""

# The captcha picture exactly as shown (drawn, not downloaded again: many captchas change on every download).
IMAGE_JS = r"""() => {
  const img = document.querySelector('[data-ken-captcha=img]');
  if (!img) return null;
  img.scrollIntoView({block: 'center'});
  try {
    if (img.tagName === 'CANVAS') return img.toDataURL('image/png');
    const c = document.createElement('canvas');
    c.width = img.naturalWidth || img.width; c.height = img.naturalHeight || img.height;
    c.getContext('2d').drawImage(img, 0, 0);
    return c.toDataURL('image/png');
  } catch (e) { const r = img.getBoundingClientRect();
    return {box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]}; }
}"""

AFTER_JS = r"""() => {
  const img = document.querySelector('[data-ken-captcha=img]'), input = document.querySelector('[data-ken-captcha=input]');
  const near = (input && (input.form || input.closest('form'))) || document.body;
  const around = near.parentElement || near;
  return {still: !!(img && img.isConnected), url: location.href, text: (around.innerText || '').replace(/\s+/g, ' ').slice(0, 500)};
}"""

TOKEN_JS = r"""() => {
  const v = s => [...document.querySelectorAll(s)].map(e => e.value).find(Boolean) || '';
  const bframe = [...document.querySelectorAll('iframe')].find(f => /\/recaptcha\/(api2|enterprise)\/bframe/.test(f.src));
  const challenge = !!(bframe && bframe.getBoundingClientRect().height > 100 && getComputedStyle(bframe.parentElement.parentElement).visibility !== 'hidden');
  return {token: v('[name="g-recaptcha-response"]') || v('[name="h-captcha-response"]') || v('[name="cf-turnstile-response"]'), challenge};
}"""


def type_answer_js(answer: str, submit: bool) -> str:
    return r"""() => {
  const el = document.querySelector('[data-ken-captcha=input]');
  if (!el) return {typed: false};
  el.focus();
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ANSWER);
  el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
  if (!SUBMIT) return {typed: true, submitted: false};
  const btn = document.querySelector('[data-ken-captcha=submit]'), form = el.form || el.closest('form');
  if (btn) btn.click(); else if (form) (form.requestSubmit ? form.requestSubmit() : form.submit());
  return {typed: true, submitted: !!(btn || form)};
}""".replace("ANSWER", json.dumps(answer)).replace("SUBMIT", "true" if submit else "false")


def token_js(kind: str, token: str, callback: str = "") -> str:
    """Put a solver's token where the widget would, and call the page's callback if it named one."""
    field = {"recaptcha": "g-recaptcha-response", "hcaptcha": "h-captcha-response", "turnstile": "cf-turnstile-response"}[kind]
    return r"""() => {
  const t = TOKEN;
  document.querySelectorAll('[name="FIELD"]').forEach(e => { e.value = t; e.innerHTML = t; });
  const cb = CALLBACK && CALLBACK.split('.').reduce((o, k) => o && o[k], window);
  if (typeof cb === 'function') cb(t);
  return {set: true, called: typeof cb === 'function'};
}""".replace("TOKEN", json.dumps(token)).replace("FIELD", field).replace("CALLBACK", json.dumps(callback))


@dataclass
class Captcha:
    kind: str  # image | recaptcha | hcaptcha | turnstile | cloudflare
    site: str
    url: str
    info: dict = field(default_factory=dict)

    @classmethod
    def from_page(cls, d: dict | None) -> "Captcha | None":
        return cls(d["kind"], d.get("site", ""), d.get("url", ""), d) if d and d.get("kind") else None


@dataclass
class Outcome:
    status: str  # solved | submitted | read_it | handoff | none
    detail: str
    captcha: Captcha | None = None
    by: str = ""
    image: str = ""  # base64 PNG to show Ken (the captcha to read, or the page at handoff)
    saved: str = ""


async def detect(browser: Browser, page_id: int) -> Captcha | None:
    return Captcha.from_page(await browser.js(page_id, DETECT_JS))


async def captcha_image(browser: Browser, page_id: int) -> tuple[str, str]:
    """(base64 PNG, note): the picture itself, or the visible page with where the picture is when the
    page won't hand it over (a picture from another site can't be copied out)."""
    got = await browser.js(page_id, IMAGE_JS)
    if isinstance(got, str) and got.startswith("data:image"):
        return got.split(",", 1)[1], ""
    box = (got or {}).get("box") if isinstance(got, dict) else None
    shot = await browser.screenshot(page_id)
    return shot, (f"The captcha is the picture at x={box[0]}, y={box[1]}, {box[2]}x{box[3]} px in this screenshot. " if box else "")


async def type_answer(browser: Browser, page_id: int, cap: Captcha, answer: str, by: str, submit: bool | None = None) -> Outcome:
    if not cap.info.get("has_input"):
        return Outcome("handoff", "Couldn't find the box to type the captcha answer into.", cap, by)
    # Only press submit on its own when the form is just the captcha: any other form is Ken's to submit.
    submit = cap.info.get("captcha_only_form", False) if submit is None else submit
    typed = await browser.js(page_id, type_answer_js(answer, submit))
    if not (typed or {}).get("typed"):
        return Outcome("handoff", "The captcha's answer box went away before the answer could be typed.", cap, by)
    if not typed.get("submitted"):
        return Outcome("submitted", f'Typed "{answer}" into the captcha box. The form has other fields, so submit it '
                       "yourself when it's ready, then check the page.", cap, by)
    await asyncio.sleep(2.5)
    try:
        after = await browser.js(page_id, AFTER_JS)
    except BrowserError:
        after = None  # the page moved on, usually a good sign
    if after is None or not after.get("still") or after.get("url") != cap.url:
        return Outcome("solved", f'Typed "{answer}" and submitted; the captcha is gone.', cap, by)
    return Outcome("submitted", f'Typed "{answer}" and submitted. The captcha is still on the page, so read what it says '
                   f"now to see if it passed: {after.get('text', '')}", cap, by)


# ------------------------------------------------------------------ solvers

class Solver:
    """One way past a captcha. `solve` returns an Outcome; anything but solved/submitted/read_it means try the next."""
    name = ""

    def handles(self, cap: Captcha) -> bool:
        raise NotImplementedError

    async def solve(self, browser: Browser, page_id: int, cap: Captcha) -> Outcome:
        raise NotImplementedError


def vision_api() -> tuple[str, dict, str] | None:
    """(Messages API base URL, auth headers, model): the person's own Anthropic key first, else the plan's included AI."""
    override = os.environ.get("KEN_CAPTCHA_VISION_MODEL", "").strip()
    if key := os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "https://api.anthropic.com", {"x-api-key": key}, override or DEFAULT_VISION_MODEL
    from onboarding import included_ai_key, included_ai_model

    if key := included_ai_key():
        return (os.environ["KEN_INCLUDED_AI_URL"].rstrip("/"), {"authorization": f"Bearer {key}"},
                override or included_ai_model() or DEFAULT_VISION_MODEL)
    return None


READ_PROMPT = ("This is a captcha from a web form. {note}Reply with only what a person would type into the box: the "
               "characters exactly as shown (same case, no spaces unless shown), or the number for a simple sum. "
               "If you can't read it, reply UNREADABLE.")


def ask_vision(api: tuple[str, dict, str], image_b64: str, note: str = "") -> str:
    base, auth, model = api
    body = {"model": model, "max_tokens": 64, "thinking": {"type": "disabled"}, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
        {"type": "text", "text": READ_PROMPT.format(note=note)}]}]}
    req = urllib.request.Request(f"{base}/v1/messages", json.dumps(body).encode(), {
        **auth, "anthropic-version": "2023-06-01", "content-type": "application/json", "user-agent": "ken-captcha"})
    with urllib.request.urlopen(req, timeout=60) as r:
        reply = json.load(r)
    text = "".join(c.get("text", "") for c in reply.get("content", []) if c.get("type") == "text").strip()
    return text.splitlines()[0].strip().strip("\"'`") if text else ""


class Vision(Solver):
    """Free: a vision model reads a text captcha; a checkbox widget gets one click."""
    name = "vision"

    def __init__(self, api=vision_api):
        self.api = api

    def handles(self, cap: Captcha) -> bool:
        return cap.kind == "image" or cap.kind in WIDGETS

    async def solve(self, browser: Browser, page_id: int, cap: Captcha) -> Outcome:
        if cap.kind in WIDGETS:
            return await self.click_box(browser, page_id, cap)
        image, note = await captcha_image(browser, page_id)
        api = self.api()
        if api is None:  # no model here to ask: Ken's own model reads it
            return Outcome("read_it", "No vision model is set up here, so read the captcha in this picture yourself and "
                           f"call solve_captcha again with page_id={page_id} and answer=<what it says>. {note}",
                           cap, self.name, image=image)
        try:
            answer = await asyncio.to_thread(ask_vision, api, image, note)
        except (urllib.error.URLError, OSError, ValueError) as e:
            return Outcome("handoff", f"The vision model couldn't be reached ({e}).", cap, self.name)
        if not answer or answer.upper() == "UNREADABLE" or len(answer) > 40:
            return Outcome("handoff", "The vision model couldn't read it.", cap, self.name)
        return await type_answer(browser, page_id, cap, answer, self.name)

    async def click_box(self, browser: Browser, page_id: int, cap: Captcha) -> Outcome:
        m = re.search(r'uid=(\S+) Iframe\w* "(reCAPTCHA|[^"]*checkbox for hCaptcha[^"]*|[^"]*Cloudflare security challenge[^"]*)"',
                      await browser.snapshot(page_id))
        if not m:
            return Outcome("handoff", "Couldn't find the captcha's checkbox.", cap, self.name)
        await browser.click(page_id, m.group(1))
        state = {}
        for _ in range(8):
            await asyncio.sleep(1)
            state = await browser.js(page_id, TOKEN_JS) or {}
            if state.get("token"):
                return Outcome("solved", "Clicked the checkbox and it passed. Carry on with the page (submit the form if "
                               "there is one).", cap, self.name)
        if state.get("challenge"):
            return Outcome("handoff", "Clicked the checkbox; it answered with a picture puzzle (it doesn't trust this "
                           "browser's IP), which the free solver doesn't do.", cap, self.name)
        return Outcome("handoff", "Clicked the checkbox; it didn't pass.", cap, self.name)


class CapSolver(Solver):
    """Paid: capsolver.com. reCAPTCHA v2 (and Enterprise) and Turnstile come back as tokens, image captchas as text."""
    name = "capsolver"
    API = "https://api.capsolver.com"

    def __init__(self, key: str, wait: float = 120, every: float = 3):
        self.key, self.wait, self.every = key, wait, every

    def handles(self, cap: Captcha) -> bool:
        return cap.kind in ("recaptcha", "turnstile", "image")

    def post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.API + path, json.dumps({"clientKey": self.key, **body}).encode(),
                                     {"content-type": "application/json", "user-agent": "ken-captcha"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    @staticmethod
    def task(cap: Captcha, image_b64: str = "") -> dict:
        i = cap.info
        if cap.kind == "image":
            return {"type": "ImageToTextTask", "body": image_b64}
        if cap.kind == "turnstile":
            t = {"type": "AntiTurnstileTaskProxyLess", "websiteURL": cap.url, "websiteKey": i.get("sitekey", "")}
            return {**t, "metadata": {"action": i["action"]}} if i.get("action") else t
        t = {"type": "ReCaptchaV2EnterpriseTaskProxyLess" if i.get("enterprise") else "ReCaptchaV2TaskProxyLess",
             "websiteURL": cap.url, "websiteKey": i.get("sitekey", "")}
        if i.get("data_s"):  # Google's own pages (e.g. /sorry/) add a one-time "data-s" value
            t = {**t, "enterprisePayload": {"s": i["data_s"]}} if i.get("enterprise") else {**t, "recaptchaDataSValue": i["data_s"]}
        return t

    async def result(self, task: dict) -> dict:
        made = await asyncio.to_thread(self.post, "/createTask", {"task": task})
        if made.get("errorId"):
            raise RuntimeError(made.get("errorDescription") or made.get("errorCode") or "createTask failed")
        if made.get("status") == "ready":
            return made.get("solution", {})
        deadline = time.monotonic() + self.wait
        while time.monotonic() < deadline:
            await asyncio.sleep(self.every)
            got = await asyncio.to_thread(self.post, "/getTaskResult", {"taskId": made["taskId"]})
            if got.get("errorId"):
                raise RuntimeError(got.get("errorDescription") or got.get("errorCode") or "getTaskResult failed")
            if got.get("status") == "ready":
                return got.get("solution", {})
        raise RuntimeError("no answer in time")

    async def solve(self, browser: Browser, page_id: int, cap: Captcha) -> Outcome:
        if cap.kind != "image" and not cap.info.get("sitekey"):
            return Outcome("handoff", "CapSolver needs the captcha's site key and the page doesn't show it.", cap, self.name)
        image = (await captcha_image(browser, page_id))[0] if cap.kind == "image" else ""
        try:
            solution = await self.result(self.task(cap, image))
        except (urllib.error.URLError, OSError, ValueError, RuntimeError, KeyError) as e:
            return Outcome("handoff", f"CapSolver didn't solve it ({e}).", cap, self.name)
        if cap.kind == "image":
            return await type_answer(browser, page_id, cap, str(solution.get("text", "")), self.name)
        token = solution.get("gRecaptchaResponse") or solution.get("token") or ""
        if not token:
            return Outcome("handoff", "CapSolver came back without a token.", cap, self.name)
        await browser.js(page_id, token_js(cap.kind, token, cap.info.get("callback", "")))
        return Outcome("solved", "CapSolver's token is in the page. Carry on (submit the form if there is one) and "
                       "check it went through; a site that also scores the IP can still say no.", cap, self.name)


PAID = {"capsolver": CapSolver}


def solvers() -> list[Solver]:
    """Free first; then the paid solver, if one is set up."""
    out: list[Solver] = [Vision()]
    name, key = os.environ.get("KEN_CAPTCHA_PROVIDER", "").strip().lower(), os.environ.get("KEN_CAPTCHA_API_KEY", "").strip()
    if name in PAID and key:
        out.append(PAID[name](key))
    return out


# ------------------------------------------------------------------ the whole thing

async def handoff(browser: Browser, page_id: int, cap: Captcha, tried: list[str], save_dir: Path | None) -> Outcome:
    image = saved = ""
    try:
        image = await browser.screenshot(page_id)
        if save_dir:
            save_dir.mkdir(parents=True, exist_ok=True)
            path = save_dir / f"captcha-{re.sub(r'[^a-z0-9.-]', '', cap.site.lower()) or 'page'}-{time.strftime('%Y%m%d-%H%M%S')}.png"
            path.write_bytes(base64.b64decode(image))
            saved = str(path)
    except (BrowserError, OSError):
        pass
    why = {"cloudflare": "Cloudflare's \"Just a moment\" check, which judges the browser and its IP, not an answer.",
           "hcaptcha": "an hCaptcha picture puzzle.",
           "recaptcha": "a reCAPTCHA."}.get(cap.kind, "a captcha.")
    detail = (f"Captcha on {cap.site}: {why} Couldn't get past it" + (f" ({'; '.join(tried)})" if tried else "") + ". "
              f"Tell them in one line: there's a captcha on {cap.site}, it needs them" + (f"; the screenshot is {saved}" if saved else "") +
              ". A text captcha they can read from the screenshot: call solve_captcha again with their answer. A checkbox or "
              "picture puzzle can't be done for them in this browser, so they'd do that step on their own device, or you try "
              "another way to the same thing (another site, an API, a page fetch).")
    return Outcome("handoff", detail, cap, image=image, saved=saved)


async def solve(browser: Browser, page_id: int, answer: str = "", submit: bool | None = None,
                using: list[Solver] | None = None, save_dir: Path | None = None) -> Outcome:
    cap = await detect(browser, page_id)
    if cap is None:
        return Outcome("none", "No captcha found on this page.")
    if answer:
        if cap.kind != "image":
            return Outcome("handoff", f"This is a {cap.kind}, not a captcha you type an answer into.", cap)
        return await type_answer(browser, page_id, cap, answer.strip(), "answer", submit)
    tried = []
    for s in using if using is not None else solvers():
        if not s.handles(cap):
            continue
        try:
            out = await s.solve(browser, page_id, cap)
        except BrowserError as e:
            out = Outcome("handoff", f"the browser said: {e}", cap, s.name)
        if out.status in ("solved", "submitted", "read_it"):
            return out
        tried.append(f"{s.name}: {out.detail}")
    return await handoff(browser, page_id, cap, tried, save_dir)


TOOL_DESCRIPTION = (
    "Get past a captcha on a page in your browser. Call it when a page shows a captcha, an \"I'm not a robot\" "
    "box, or an unusual-traffic check, with that page's id (list_pages). It finds the captcha and tries a vision "
    "model, then a paid solver if one is set up, and otherwise tells you to hand it to them, with a screenshot. "
    "Pass `answer` to type the answer to a text captcha (one you or they read); `submit` to press the form's "
    "button after typing (by default only when the form is just the captcha). Only for their own errands, never "
    "to get around a site that has said no to automated use.")


def server(browser_server: dict, save_dir: Path):
    """The `solve_captcha` tool, as an in-process MCP server."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    async def run(args):
        try:
            out = await solve(Browser(browser_server), int(args["page_id"]), str(args.get("answer") or ""),
                              args.get("submit"), save_dir=save_dir)
        except Exception as exc:  # the tool answers; it never takes the chat down
            return {"content": [{"type": "text", "text": f"Failed: {exc}"}], "is_error": True}
        head = f"[{out.status}{' by ' + out.by if out.by else ''}{' · ' + out.captcha.kind + ' on ' + out.captcha.site if out.captcha else ''}] "
        content = [{"type": "text", "text": head + out.detail}]
        if out.image:
            content.append({"type": "image", "data": out.image, "mimeType": "image/png"})
        return {"content": content}

    return create_sdk_mcp_server("captcha", tools=[tool("solve_captcha", TOOL_DESCRIPTION, {
        "type": "object", "properties": {"page_id": {"type": "integer"}, "answer": {"type": "string"},
                                         "submit": {"type": "boolean"}}, "required": ["page_id"]})(run)])


def external_server_from_env(home: Path):
    """The same server in its own process (for Codex): bot.py hands it the browser's address in the environment."""
    return server(json.loads(os.environ["KEN_CAPTCHA_BROWSER"]), Path(os.environ.get("KEN_CAPTCHA_DIR") or home / "work") / "captchas")
