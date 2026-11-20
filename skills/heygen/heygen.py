#!/usr/bin/env python3
"""HeyGen for Ken: a presenter video of their own avatar from a script. Python standard library only (no installs).

Two ways in, the same as the ElevenLabs helper. Their own key from HEYGEN_API_KEY (never printed) always wins when
they added one, even on hosted Ken. Without it, on hosted Ken (the computer has a host: KEN_INCLUDED_AI_URL/TOKEN, or
KEN_HEYGEN_URL/TOKEN) every call goes through the host's /box/heygen, which uses Ken's own HeyGen account with a
monthly allowance per person. A key that runs out of credits never falls back to Ken's account. Every command prints
one JSON line on success; errors go to stderr.

    heygen.py check                                   whose account, and what's left
    heygen.py avatars [--stock]                       their own avatar looks (id, name, kind, ready); --stock: HeyGen's
    heygen.py voices [--stock] [--language en] [--query calm]   their own voices; --stock: HeyGen's
    heygen.py estimate (--script "..." | --script-file f)       how long the video will run, and what's left
    heygen.py generate --avatar LOOK_ID (--script "..." | --script-file f) --said "yes, that's my avatar" --own-avatar
                       --out videos/x/avatar.mp4 [--voice ID] [--aspect 9:16] [--resolution 1080p] [--no-wait]
    heygen.py status VIDEO_ID                         where a video is
    heygen.py get VIDEO_ID --out videos/x/avatar.mp4  wait for it and download it (never makes it again)

HeyGen's v3 API (https://developers.heygen.com, read Nov 20 2026; v1 and v2 were retired Oct 31 2026).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KEY_HELP = ("No HeyGen key. Ask them to add it in the Brain tab, under Accounts & passwords, named HEYGEN_API_KEY "
            "(app.heygen.com → Settings → API). It works from their next message.")
NOT_LIVE = ("Ken's built-in HeyGen isn't switched on for this computer yet, so nothing ran. If they have their own "
            "HeyGen account, ask them to add its key in the Brain tab, under Accounts & passwords, named "
            "HEYGEN_API_KEY: it runs on that from their next message.")
OWN_CREDITS_GONE = "Their own HeyGen credits are used up"
MAX_SCRIPT = 5000  # characters a video (developers.heygen.com/docs/usage-limits)
WORDS_PER_MIN = 150  # for the estimate, when their own pace isn't given
OUT_OF_CREDIT = {"insufficient_credit", "usage_limit_reached", "trial_limit_exceeded", "subscription_required",
                 "plan_upgrade_required", "quota_exceeded"}
QUOTA_LEFT: list[str] = []  # what the host said is left after the last call ("240 seconds")


class Fail(SystemExit):
    def __init__(self, msg: str):
        super().__init__(1)
        self.msg = msg


class _NotLive(Exception):
    """The host answered, but not as /box/heygen (a redirect to the homepage, a page, a 404): the route isn't live."""


def api() -> str:
    return os.environ.get("HEYGEN_BASE_URL", "https://api.heygen.com").rstrip("/")


def own_key() -> str:
    return os.environ.get("HEYGEN_API_KEY", "").strip()


def host_route() -> tuple[str, str] | None:
    """Hosted Ken's /box/heygen and this computer's token for it, whether or not their own key is in use."""
    url = os.environ.get("KEN_HEYGEN_URL", "").strip().rstrip("/")
    if not url:
        ai = os.environ.get("KEN_INCLUDED_AI_URL", "").strip().rstrip("/")
        if ai.startswith("https://") and ai.endswith("/ai"):
            url = ai[: -len("/ai")] + "/box/heygen"
    token = (os.environ.get("KEN_HEYGEN_TOKEN") or os.environ.get("KEN_INCLUDED_AI_TOKEN") or "").strip()
    return (url, token) if token and (url.startswith("https://") or _local(url)) else None


def host() -> tuple[str, str] | None:
    """Where the calls go: the host, or None (no host, or they added their own key, which always wins)."""
    return None if own_key() else host_route()


def _local(url: str) -> bool:
    return url.startswith(("http://localhost", "http://127.0.0.1"))  # a test copy


def _redact(text: str) -> str:
    for name in ("HEYGEN_API_KEY", "KEN_HEYGEN_TOKEN", "KEN_INCLUDED_AI_TOKEN"):
        k = os.environ.get(name, "")
        text = text.replace(k, "[key]") if len(k) > 4 else text
    return text


# ---------- HTTP (urllib, so it runs on any Python Ken has) ----------

def call(method: str, path: str, *, query: dict | None = None, body: dict | None = None, headers: dict | None = None,
         timeout: int = 60):
    """One API call, through the host or straight to HeyGen with their key. Returns the parsed JSON. Errors become a
    readable Fail; nothing is retried, and it never switches to the other account on its own."""
    via = host()
    if not via and not own_key():
        raise Fail(KEY_HELP)
    base = via[0] if via else api()
    if not (base.startswith("https://") or _local(base)):
        raise Fail("HEYGEN_BASE_URL must be https.")
    url = base + path + ("?" + urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None}) if query else "")
    # A named client: Cloudflare in front of the host refuses Python's default "Python-urllib".
    sent = {"User-Agent": "Ken", "Authorization": f"Bearer {via[1]}"} if via else {"X-Api-Key": own_key(), "User-Agent": "ken-heygen/1"}
    sent.update(headers or {})
    data = None
    if body is not None:
        data, sent["Content-Type"] = json.dumps(body).encode(), "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=sent, method=method), timeout=timeout) as r:
            raw, answered, moved = r.read(), r.headers, r.geturl()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        if via and _host_page(e.code, detail):
            raise Fail(NOT_LIVE)
        raise Fail(_explain_host(e.code, detail) if via else _explain(e.code, _redact(detail), e.headers.get("Retry-After")))
    except (urllib.error.URLError, TimeoutError) as e:
        where = "Ken's built-in HeyGen" if via else "HeyGen"
        raise Fail(f"Couldn't reach {where} ({getattr(e, 'reason', e)}), so nothing ran. Tell them in one line; try "
                   "again later, not in a loop.")
    if via:
        if answered.get("x-ken-quota-left"):
            QUOTA_LEFT[:] = [answered["x-ken-quota-left"]]
        # A redirect (the host sends unknown paths to its homepage) or a web page instead of the answer: not the route.
        if moved.split("?")[0] != url.split("?")[0] or "text/html" in (answered.get("content-type") or ""):
            raise Fail(NOT_LIVE)
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise Fail(NOT_LIVE if via else f"HeyGen sent something that isn't an answer ({len(raw)} bytes); try once more later, not in a loop.")


def _host_page(code: int, detail: str) -> bool:
    """An error from the host that isn't from its HeyGen route (which always answers {"error", "code"})."""
    if 300 <= code < 400:
        return True
    if code not in (404, 405, 501):
        return False
    try:
        return not isinstance(json.loads(detail).get("code"), str)
    except (ValueError, TypeError, AttributeError):
        return True


def _explain_host(code: int, detail: str) -> str:
    """The host's answer ({"error", "code"}), for the chat: what happened and what to do, never a price."""
    try:
        said = json.loads(detail)
    except ValueError:
        said = {}
    error, kind = _redact(str(said.get("error") or detail)), said.get("code")
    if kind == "quota":
        return (f"Allowance used up: {error} Tell them in one line; if they have their own HeyGen account, adding its "
                "key in the Brain tab under Accounts & passwords as HEYGEN_API_KEY runs everything on that instead.")
    if kind in ("heygen_down", "busy"):
        return f"{error} Don't retry in a loop."
    return f"{error} ({code})"


def _explain(code: int, detail: str, retry_after: str | None = None) -> str:
    """HeyGen's answer ({"error": {"code", "message"}}): branch on the code, the HTTP status alone misleads (a 429 is
    either too fast or out of quota)."""
    try:
        said = json.loads(detail).get("error") or {}
    except (ValueError, AttributeError):
        said = {}
    said = said if isinstance(said, dict) else {"message": str(said)}
    kind, message = str(said.get("code") or ""), str(said.get("message") or detail)
    if kind in OUT_OF_CREDIT or code == 402:
        # Their key, their credits: never quietly switch to Ken's account while their key is set.
        fix = ("top up on app.heygen.com (Settings → API → billing), or remove HEYGEN_API_KEY from Brain (Accounts & "
               "passwords) to use Ken's included allowance instead" if host_route()
               else "top up on app.heygen.com (Settings → API → billing), or wait for the plan to reset")
        return f"{OWN_CREDITS_GONE} ({kind or code}: {message}): {fix}. Tell them exactly that in one line; don't retry."
    hint = {
        "unauthorized": "the key is wrong or was revoked: ask them to check HEYGEN_API_KEY in Brain",
        "insufficient_api_key_scope": "their key isn't allowed to do this: in HeyGen, Settings → API, give the key this permission",
        "rate_limit_exceeded": f"too many calls: wait {retry_after or 60} seconds, don't retry in a loop",
        "avatar_not_found": "no avatar with that id on this account: run `avatars` and use an id from it",
        "voice_not_found": "no voice with that id on this account: run `voices` and use an id from it",
        "video_not_found": "no video with that id on this account",
        "not_found": "nothing with that id on this account",
        "resource_not_ready": "the avatar is still being made: try again when `avatars` shows it ready",
        "avatar_consent_required": "HeyGen has no consent recording for this avatar yet: they finish it in the HeyGen app, on the avatar's page",
        "avatar_not_usable": "this avatar can't make videos (failed or expired): they check it in the HeyGen app",
        "content_policy_violation": "HeyGen refused the script (its content rules): tell them, don't reword it to get around it",
        "endpoint_retired": "HeyGen retired this call: Ken's HeyGen skill needs an update",
    }.get(kind) or {401: "the key is wrong or was revoked: ask them to check HEYGEN_API_KEY in Brain",
                    429: "too many calls: wait a minute, don't retry in a loop"}.get(code, "")
    return f"HeyGen said {code}" + (f" {kind}" if kind else "") + (f" ({hint})" if hint else "") + f": {message}"


def pages(path: str, query: dict, most: int = 200) -> list[dict]:
    """A whole list ({"data": [...], "has_more", "next_token"}), a page at a time."""
    out: list[dict] = []
    token = None
    while len(out) < most:
        page = call("GET", path, query={**query, "token": token})
        out += [x for x in page.get("data") or [] if isinstance(x, dict)]
        token = page.get("next_token")
        if not page.get("has_more") or not token:
            break
    return out


def done(**info) -> dict:
    if host():  # Ken's account: only what's left
        info["account"] = "ken"
        if QUOTA_LEFT:
            info["quota_left"] = _readable(QUOTA_LEFT[0])
    else:
        info["account"] = "own"
        if host_route():
            info["account_note"] = "Ran on their own HeyGen key (their credits): it wins over Ken's allowance while it's in Brain."
    print(json.dumps(info, ensure_ascii=False))
    return info


def _readable(left: str) -> str:
    """"540 seconds" → "9 min"; other units as they are."""
    n, _, unit = left.partition(" ")
    if unit == "seconds" and n.isdigit():
        return f"{int(n) / 60:.1f}".rstrip("0").rstrip(".") + " min"
    return left


# ---------- what's theirs ----------

def own_looks() -> list[dict]:
    """Their own avatar looks (ownership=private is the only way HeyGen tells theirs from its stock ones), each with
    its group's consent."""
    looks = pages("/v3/avatars/looks", {"ownership": "private", "limit": 50})
    groups = {g.get("id"): g for g in pages("/v3/avatars", {"ownership": "private", "limit": 50})} if looks else {}
    for look in looks:
        g = groups.get(look.get("group_id")) or {}
        look["consent_status"] = g.get("consent_status")
        look["group_status"] = g.get("status")
    return looks


def _ready(look: dict) -> bool:
    return (look.get("status") in (None, "completed") and look.get("group_status") in (None, "completed")
            and look.get("consent_status") in (None, "accepted"))


def _look(x: dict) -> dict:
    out = {"id": x.get("id"), "name": x.get("name"), "kind": x.get("avatar_type"), "orientation": x.get("preferred_orientation"),
           "voice": x.get("default_voice_id"), "preview": x.get("preview_image_url")}
    return {k: v for k, v in out.items() if v is not None}


def script_of(a) -> str:
    text = Path(a.script_file).expanduser().read_text(encoding="utf-8") if a.script_file else (a.script or "")
    text = text.strip()
    if not text:
        raise Fail("The script is empty.")
    if len(text) > MAX_SCRIPT:
        raise Fail(f"The script is {len(text)} characters; HeyGen takes {MAX_SCRIPT} a video. Split it into parts and "
                   "join the videos with ffmpeg.")
    return text


def seconds_for(text: str, wpm: float) -> int:
    return max(1, round(len(text.split()) / max(wpm, 60) * 60))


def balance() -> dict:
    """What's left: Ken's allowance (the host), or their own HeyGen balance."""
    if host():
        q = call("GET", "/quota")
        left = {f: _readable(f"{v.get('left')} {v.get('unit')}") for f, v in (q.get("features") or {}).items() if isinstance(v, dict)}
        return {"left": left, "left_seconds": ((q.get("features") or {}).get("avatar_video") or {}).get("left"), "resets": q.get("resets")}
    me = call("GET", "/v3/users/me").get("data") or {}
    kind = me.get("billing_type")
    out: dict = {"billing": kind}
    if kind == "wallet" and me.get("wallet"):
        out["balance"] = f"{me['wallet'].get('remaining_balance')} {me['wallet'].get('currency')}"
    elif kind == "subscription" and me.get("subscription"):
        credits = me["subscription"].get("credits") or {}
        out["plan"] = me["subscription"].get("plan")
        out["credits"] = {k: (v or {}).get("remaining") for k, v in credits.items()}
    elif kind == "usage_based" and me.get("usage_based"):
        u = me["usage_based"]
        out["spent_usd"], out["cap_usd"], out["credits"] = u.get("spending_current_usd"), u.get("spending_cap_usd"), u.get("remaining_credits")
    return out


# ---------- commands ----------

def cmd_check(a):
    return done(**balance())


def cmd_avatars(a):
    if a.stock:
        return done(stock=True, avatars=[_look(x) for x in pages("/v3/avatars/looks", {"ownership": "public", "limit": 50}, most=a.limit)],
                    note="HeyGen's stock presenters, not them. `generate` only takes their own avatar.")
    looks = own_looks()
    out = [{**_look(x), "ready": _ready(x), **({"waiting_on": "consent"} if x.get("consent_status") not in (None, "accepted") else {})} for x in looks]
    return done(avatars=out, **({} if out else {"note": "They have no avatar of their own on this account yet. They make one in the "
                                                        "HeyGen app (Avatars → Create), which records their consent on camera."}))


def cmd_voices(a):
    q = {"type": "public" if a.stock else "private", "limit": 50 if a.stock else 100}
    if a.stock:
        q.update(language=a.language, gender=a.gender, query=a.query)
    voices = [{"id": v.get("voice_id"), "name": v.get("name"), "language": v.get("language"), "gender": v.get("gender"),
               "kind": v.get("mode"), "ready": v.get("status") in (None, "ACTIVE")} for v in pages("/v3/models/audio/voices", q, most=a.limit)]
    return done(stock=bool(a.stock), voices=voices)


def cmd_estimate(a):
    text = script_of(a)
    secs = seconds_for(text, a.wpm)
    info = {"seconds": secs, "words": len(text.split()), "characters": len(text), **balance()}
    left = info.pop("left_seconds", None)
    if isinstance(left, (int, float)):
        info["fits"] = secs <= left
    return done(**info)


def cmd_generate(a):
    text = script_of(a)
    if not a.own_avatar or len((a.said or "").strip()) < 2:
        raise Fail("Only their own avatar. Ask once in chat (\"this is your own HeyGen avatar and voice, and you want a "
                   "video made with it?\"), then pass their yes as --said \"...\" with --own-avatar.")
    if a.out and not a.no_wait and Path(a.out).expanduser().exists() and not a.force:
        raise Fail(f"{a.out} is already there. Pick another name (--force replaces it).")
    looks = own_looks()
    look = next((x for x in looks if x.get("id") == a.avatar), None)
    if not look:
        raise Fail("That avatar isn't one of their own on this account, so nothing was made. `avatars` lists theirs; a "
                   "stock presenter or someone else's avatar is not for this skill.")
    if not _ready(look):
        raise Fail("Their avatar isn't ready yet (still being made, or its consent recording isn't accepted). They "
                   "finish it in the HeyGen app; `avatars` shows when it's ready.")
    if a.voice and not any(v.get("voice_id") == a.voice for v in pages("/v3/models/audio/voices", {"type": "private", "limit": 100})):
        raise Fail("That voice isn't one of their own on this account. Leave --voice out to use the avatar's own voice.")
    if not a.voice and not look.get("default_voice_id"):
        raise Fail("Their avatar has no voice of its own. `voices` lists their voices: pass one with --voice.")
    body = {"type": "avatar", "avatar_id": a.avatar, "script": text, "aspect_ratio": a.aspect, "resolution": a.resolution}
    if a.voice:
        body["voice_id"] = a.voice
    if a.title:
        body["title"] = a.title[:100]
    if a.speed:
        body["voice_settings"] = {"speed": a.speed}
    secs = seconds_for(text, a.wpm)
    # The same request twice (a retry after a dropped connection) is one video, paid once: HeyGen replays the first
    # answer for a day.
    headers = {"Idempotency-Key": "ken-" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:40]}
    if host():
        headers.update({"X-Ken-Seconds": str(secs), "X-Ken-Consent": urllib.parse.quote(a.said.strip()[:200])})
    made = call("POST", "/v3/videos", body=body, headers=headers).get("data") or {}
    vid = made.get("video_id")
    if not vid:
        raise Fail("HeyGen took the request but gave no video id; check `avatars` and try once more, not in a loop.")
    note = {"video_id": vid, "avatar_id": a.avatar, "avatar": look.get("name"), "voice_id": a.voice or look.get("default_voice_id"),
            "aspect": a.aspect, "resolution": a.resolution, "said": a.said.strip(), "own_avatar": True,
            "made_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "script": text}
    if a.out:
        out = Path(a.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".heygen.json").write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.no_wait or not a.out:
        return done(video_id=vid, status=made.get("status") or "pending", est_seconds=secs,
                    next=f"get {vid} --out <file.mp4> waits for it and downloads it")
    return fetch(vid, Path(a.out).expanduser(), a.timeout, a.every)


def cmd_status(a):
    v = call("GET", f"/v3/videos/{urllib.parse.quote(a.video_id, safe='')}").get("data") or {}
    info = {"video_id": a.video_id, "status": v.get("status"), "seconds": v.get("duration")}
    if v.get("status") == "failed":
        info["why"] = f"{v.get('failure_code') or 'failed'}: {v.get('failure_message') or ''}".strip(": ")
    return done(**{k: x for k, x in info.items() if x is not None})  # the download link is signed: never printed


def cmd_get(a):
    out = Path(a.out).expanduser()
    if out.exists() and not a.force:
        raise Fail(f"{a.out} is already there. Pick another name (--force replaces it).")
    return fetch(a.video_id, out, a.timeout, a.every)


def fetch(vid: str, out: Path, timeout: float, every: float) -> dict:
    """Wait for a video and download it. Slow is not failed: when time runs out the video is still being made, and
    `get` picks it up later. It is never made again."""
    until = time.monotonic() + timeout
    while True:
        v = call("GET", f"/v3/videos/{urllib.parse.quote(vid, safe='')}").get("data") or {}
        state = v.get("status")
        if state == "completed" and v.get("video_url"):
            break
        if state == "failed":
            raise Fail(f"HeyGen couldn't make the video ({v.get('failure_code') or 'failed'}: {v.get('failure_message') or 'no reason given'}). "
                       "Tell them in one line; don't make it again without asking.")
        if time.monotonic() + every > until:
            raise Fail(f"Still being made after {round(timeout / 60)} min (HeyGen is slow, not failed). Don't generate it "
                       f"again: `get {vid} --out {out}` picks it up later.")
        time.sleep(every)
    url = str(v["video_url"])
    if not (url.startswith("https://") or _local(url)):
        raise Fail("HeyGen's download link isn't https, so it wasn't fetched.")
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    try:  # a signed link on another host: no key goes with it
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ken-heygen/1"}), timeout=600) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        part.unlink(missing_ok=True)
        raise Fail(f"The video is ready but the download failed ({getattr(e, 'reason', e)}). `get {vid} --out {out}` "
                   "fetches it again; it isn't made twice.")
    part.replace(out)
    info = {"file": str(out), "video_id": vid, "seconds": v.get("duration"), "bytes": out.stat().st_size, **probe(out)}
    return done(**{k: x for k, x in info.items() if x is not None})


def probe(path: Path) -> dict:
    """Width, height and whether it's vertical, when ffprobe is here."""
    if not shutil.which("ffprobe"):
        return {}
    try:
        got = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0:s=x", str(path)], capture_output=True, text=True, check=True, timeout=60).stdout.strip()
        w, h = (int(n) for n in got.split("x")[:2])
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"warning": "ffprobe couldn't read the file: look at it before sending"}
    return {"width": w, "height": h, "vertical": h > w}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="heygen.py", description="HeyGen for Ken: a presenter video of their own avatar.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(run=cmd_check)
    s = sub.add_parser("avatars")
    s.add_argument("--stock", action="store_true")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(run=cmd_avatars)
    s = sub.add_parser("voices")
    s.add_argument("--stock", action="store_true")
    s.add_argument("--language")
    s.add_argument("--gender", choices=["male", "female"])
    s.add_argument("--query")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(run=cmd_voices)

    def script_args(s):
        g = s.add_mutually_exclusive_group(required=True)
        g.add_argument("--script")
        g.add_argument("--script-file")
        s.add_argument("--wpm", type=float, default=WORDS_PER_MIN, help="their speaking pace, from style.md")

    def wait_args(s):
        s.add_argument("--timeout", type=float, default=1500, help="seconds to wait for HeyGen")
        s.add_argument("--every", type=float, default=10, help="seconds between looks")
        s.add_argument("--force", action="store_true", help="replace --out if it's there")

    s = sub.add_parser("estimate")
    script_args(s)
    s.set_defaults(run=cmd_estimate)
    s = sub.add_parser("generate")
    s.add_argument("--avatar", required=True, help="a look id from `avatars`")
    script_args(s)
    s.add_argument("--voice", help="one of their own voices; default: the avatar's own")
    s.add_argument("--aspect", default="9:16", choices=["9:16", "16:9", "1:1", "4:5", "5:4", "auto"])
    s.add_argument("--resolution", default="1080p", choices=["720p", "1080p", "4k"])
    s.add_argument("--speed", type=float)
    s.add_argument("--title")
    s.add_argument("--said", help="their yes in chat, as they wrote it")
    s.add_argument("--own-avatar", action="store_true", help="they confirmed the avatar and voice are their own")
    s.add_argument("--out")
    s.add_argument("--no-wait", action="store_true")
    wait_args(s)
    s.set_defaults(run=cmd_generate)
    s = sub.add_parser("status")
    s.add_argument("video_id")
    s.set_defaults(run=cmd_status)
    s = sub.add_parser("get")
    s.add_argument("video_id")
    s.add_argument("--out", required=True)
    wait_args(s)
    s.set_defaults(run=cmd_get)
    a = p.parse_args(argv)
    try:
        a.run(a)
    except Fail as e:
        print(_redact(e.msg), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
