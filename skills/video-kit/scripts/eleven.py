#!/usr/bin/env python3
"""ElevenLabs for the video kit: one small command per feature, Python standard library only (no installs).

Two ways in. On hosted Ken (the computer has a host: KEN_INCLUDED_AI_URL/TOKEN, or KEN_ELEVEN_URL/TOKEN) every call
goes through the host's /box/eleven, which uses Ken's own ElevenLabs account with a monthly allowance per person: no
key here, nothing for them to pay. Anywhere else, their own key from ELEVENLABS_API_KEY (never printed).
KEN_ELEVEN=own uses their own key even on hosted Ken, and so does a host without /box/eleven yet. Every command
prints one JSON line on success (the files it wrote, the job id, and the estimated cost on their key or what's left
of the allowance on Ken's); errors go to stderr.

    eleven.py check                                      what's left (Ken's allowance, or their plan's credits)
    eleven.py voices [--search calm]                     their voices (id, name, kind)
    eleven.py estimate <feature> [file] [--chars N]      what a run would cost, before spending anything
    eleven.py tts --voice ID (--text "..." | --text-file f) --out vo.mp3 [--model eleven_multilingual_v2]
    eleven.py clone --name "Swap" --sample a.mp3 [--sample b.mp3] --said "yes it's my voice" --own-voice
    eleven.py changer --voice ID --in take.mp3 --out new.mp3
    eleven.py isolate --in noisy.mp4 --out clean.mp4     a video gets its cleaned audio put back in
    eleven.py sfx --text "door slam" [--seconds 2] --out door.mp3
    eleven.py music --prompt "lofi beat" --seconds 30 --out bed.mp3 [--instrumental]
    eleven.py captions --in video.mp4 --out video.srt [--lang hi] [--speakers]
    eleven.py design --describe "warm, older Indian man" [--text "..."] --out-dir previews/
    eleven.py design-save --id GENERATED_ID --name "Narrator" --describe "..."
    eleven.py dub --in reel.mp4 --to hi [--from en] --out reel.hi.mp4 [--v2] [--speakers 1]
    eleven.py dub-start / dub-status ID / dub-get ID --to hi --out f    the same, in steps (a long video)
    eleven.py voice-delete ID                            delete a voice they made (frees a slot)
    eleven.py over --video broll.mp4 [--video b2.mp4] --audio vo.mp3 --out source.mp4 [--bed 0.1]   voiceover under footage
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API = os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io")
KEY_HELP = ("No ElevenLabs key. Ask them to add it in the Brain tab, under Accounts & passwords, "
            "named ELEVENLABS_API_KEY (elevenlabs.io → Developers → API keys).")
HOST_MAX_UPLOAD = 95 * 1024 * 1024  # the host takes 100 MB a request
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".mpeg", ".3gp"}

# USD from https://elevenlabs.io/pricing/api (read Nov 17 2026). API usage is billed in dollars; on a plan it comes out of
# the plan's credits first. Estimates only: check `check` for what's actually left.
PRICE = {
    "tts": ("per_1k_chars", 0.08),        # eleven_v3 / multilingual_v2 / v4
    "tts_flash": ("per_1k_chars", 0.04),  # eleven_flash_v2_5
    "changer": ("per_min", 0.12),
    "isolate": ("per_min", 0.12),
    "sfx": ("per_min", 0.12),
    "music": ("per_min", 0.15),
    "captions": ("per_min", 0.20 / 60),   # Scribe: $0.20 an hour
    "dub": ("per_min", 0.50),             # dubbing v1 without watermark (v1 with watermark $0.33)
    "dub_v2": ("per_min", 2.20),
    "clone": ("flat", 0.0),               # instant clone: free on a paid plan (Starter and up)
    "design": ("flat", 0.0),              # unverified: previews are short TTS
}


class Fail(SystemExit):
    def __init__(self, msg: str):
        super().__init__(1)
        self.msg = msg


def key() -> str:
    k = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not k:
        raise Fail(KEY_HELP)
    return k


def host() -> tuple[str, str] | None:
    """Hosted Ken's /box/eleven and this computer's token for it, or None (no host, KEN_ELEVEN=own, or the route
    isn't live and they have their own key)."""
    if os.environ.get("KEN_ELEVEN", "").strip().lower() == "own" or HOST_NOT_LIVE:
        return None
    url = os.environ.get("KEN_ELEVEN_URL", "").strip().rstrip("/")
    if not url:
        ai = os.environ.get("KEN_INCLUDED_AI_URL", "").strip().rstrip("/")
        if ai.startswith("https://") and ai.endswith("/ai"):
            url = ai[: -len("/ai")] + "/box/eleven"
    token = (os.environ.get("KEN_ELEVEN_TOKEN") or os.environ.get("KEN_INCLUDED_AI_TOKEN") or "").strip()
    local = url.startswith(("http://localhost", "http://127.0.0.1"))  # a test copy of the host
    return (url, token) if token and (url.startswith("https://") or local) else None


QUOTA_LEFT: list[str] = []  # what the host said is left after the last call ("540 seconds")
HOST_NOT_LIVE: list[bool] = []  # set when the host has no /box/eleven yet and their own key took over
NOT_LIVE = ("Ken's built-in ElevenLabs isn't switched on for this computer yet, so nothing ran. If they have their own "
            "ElevenLabs key, ask them to add it in the Brain tab, under Accounts & passwords, named ELEVENLABS_API_KEY: "
            "it runs on that from their next message.")


class _NotLive(Exception):
    """The host answered, but not as /box/eleven (a redirect to the homepage, a page, a 404): the route isn't live."""


def _redact(text: str) -> str:
    for name in ("ELEVENLABS_API_KEY", "KEN_ELEVEN_TOKEN", "KEN_INCLUDED_AI_TOKEN"):
        k = os.environ.get(name, "")
        text = text.replace(k, "[key]") if len(k) > 4 else text
    return text


# ---------- HTTP (urllib, so it runs on any Python Ken has) ----------

def multipart(fields: dict, files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    """fields: name → str or list of str; files: (field name, path). Returns body and content type."""
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        for v in value if isinstance(value, list) else [value]:
            if v is None:
                continue
            out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            out += (v if isinstance(v, str) else json.dumps(v)).encode() + b"\r\n"
    for name, path in files:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode()
        out += path.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def call(method: str, path: str, *, query: dict | None = None, json_body: dict | None = None,
         fields: dict | None = None, files: list | None = None, raw: bool = False, timeout: int = 600,
         seconds: float | None = None, headers: dict | None = None, host_only: bool = False):
    """One API call (through the host, or straight to ElevenLabs with their key). Returns parsed JSON, or bytes when
    raw=True. Errors become a readable Fail. seconds: how long the uploaded media is (the host counts it).
    When the host has no ElevenLabs route yet and they have their own key, the call goes to ElevenLabs on that key
    (host_only: a path only the host has, like /quota: None instead)."""
    via = host()
    if via and files and path != "/quota" and call("GET", "/quota", host_only=True) is None:
        via = None  # a quick look first, so a video isn't sent to a host that can't take it and then sent again
    try:
        return _call(via, method, path, query=query, json_body=json_body, fields=fields, files=files, raw=raw,
                     timeout=timeout, seconds=seconds, headers=headers)
    except _NotLive as e:
        if not os.environ.get("ELEVENLABS_API_KEY", "").strip():
            raise Fail(str(e) or NOT_LIVE)
        HOST_NOT_LIVE[:] = [True]
        if host_only:
            return None
        return _call(None, method, path, query=query, json_body=json_body, fields=fields, files=files, raw=raw,
                     timeout=timeout, seconds=seconds, headers=headers)


def _call(via, method, path, *, query, json_body, fields, files, raw, timeout, seconds, headers):
    url = (via[0] if via else API) + path + ("?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None}) if query else "")
    # A named client: Cloudflare in front of the host refuses Python's default "Python-urllib".
    sent = {"User-Agent": "Ken"} if via else {"xi-api-key": key(), "User-Agent": "ken-video-kit/1"}
    if via:
        sent["Authorization"] = f"Bearer {via[1]}"
        if seconds is not None:
            sent["X-Ken-Seconds"] = f"{max(seconds, 0.1):.2f}"
        sent.update(headers or {})
    data = None
    if json_body is not None:
        data, sent["Content-Type"] = json.dumps(json_body).encode(), "application/json"
    elif fields is not None or files:
        data, sent["Content-Type"] = multipart(fields or {}, files or [])
    if via and data and len(data) > HOST_MAX_UPLOAD:
        raise Fail(f"That's {len(data) // 1048576} MB to send; 95 MB is the most at once. Cut it into parts first.")
    req = urllib.request.Request(url, data=data, headers=sent, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            answered = getattr(r, "headers", None) or {}
            moved = r.geturl() if hasattr(r, "geturl") else url
            left = answered.get("x-ken-quota-left")
            if left:
                QUOTA_LEFT[:] = [left]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        if via and _host_page(e.code, detail):
            raise _NotLive()
        raise Fail(_explain_host(e.code, detail) if via else _explain(e.code, _redact(detail)))
    except urllib.error.URLError as e:
        if via:
            raise _NotLive(f"Couldn't reach Ken: {e.reason}")
        raise Fail(f"Couldn't reach ElevenLabs: {e.reason}")
    # A redirect (the host sends unknown paths to its homepage) or a web page instead of the answer: not the route.
    if via and (moved.split("?")[0] != url.split("?")[0] or "text/html" in (answered.get("content-type") or "")):
        raise _NotLive()
    if raw:
        return body
    try:
        return json.loads(body or b"{}")
    except ValueError:
        if via:
            raise _NotLive()
        raise Fail(f"ElevenLabs sent something that isn't an answer ({len(body)} bytes); try once more later, not in a loop.")


def _host_page(code: int, detail: str) -> bool:
    """An error from the host that isn't from its ElevenLabs route (which always answers {"error", "code"}). A redirect
    urllib won't follow (a DELETE) is one too."""
    if 300 <= code < 400:
        return True
    if code not in (404, 405, 501):
        return False
    try:
        return "code" not in json.loads(detail)
    except (ValueError, TypeError):
        return True


def _explain_host(code: int, detail: str) -> str:
    """The host's answer ({"error", "code"}), for the chat: what happened and what to do, never a price."""
    try:
        said = json.loads(detail)
    except ValueError:
        said = {}
    error, kind = _redact(str(said.get("error") or detail)), said.get("code")
    if kind == "quota":
        return f"Allowance used up: {error} Tell them in one line; if they have their own ElevenLabs key, it can run on that (KEN_ELEVEN=own)."
    if kind in ("eleven_down", "busy"):
        return f"{error} Don't retry in a loop."
    return f"{error} ({code})"


def _explain(code: int, detail: str) -> str:
    # ElevenLabs says what went wrong in detail.status, and the HTTP code alone misleads: a wrong key is a 400, and
    # running out of credits is a 401 like a wrong key.
    try:
        said = json.loads(detail).get("detail")
    except (ValueError, AttributeError):
        said = None
    status = str(said.get("status") or said.get("code") or "") if isinstance(said, dict) else ""
    message = str(said.get("message") or "") if isinstance(said, dict) else ""
    if status == "quota_exceeded":
        return ("ElevenLabs: out of credits on their plan (" + (message or "quota exceeded") + "). Tell them in one line; "
                "it comes back when the plan resets, or with a bigger plan. Don't retry.")
    if status in ("missing_permissions", "missing_group_permissions"):
        perms = ", ".join((said.get("data") or {}).get(status) or []) or "this feature"
        return (f"ElevenLabs: their key isn't allowed to do this ({perms}). It's a setting, not their plan: in ElevenLabs, "
                "Developers → API keys, edit the key and turn on that permission (or, in a team workspace, ask its admin "
                "for it). Tell them in one line; don't retry.")
    if status in ("invalid_api_key", "invalid_api_key_length") or (isinstance(said, dict) and said.get("type") == "authentication_error"):
        code = 401
    hint = {401: "the key is wrong or was revoked: ask them to check ELEVENLABS_API_KEY in Brain",
            402: "out of credits or the plan doesn't include this",
            403: "the plan or the key's permissions don't include this (voice cloning and music need a paid plan)",
            413: "the file is too big for this feature",
            429: "too many jobs at once or rate limited: wait a minute, don't retry in a loop"}.get(code, "")
    return f"ElevenLabs said {code}" + (f" ({hint})" if hint else "") + f": {message or detail}"


def download(url: str, out: Path) -> Path:
    """A signed URL (no key sent: it's a different host)."""
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ken-video-kit/1"}), timeout=600) as r:
        out.write_bytes(r.read())
    return out


# ---------- media helpers (ffmpeg) ----------

def duration(path: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except (OSError, subprocess.CalledProcessError, ValueError):
        raise Fail(f"Couldn't read how long {path.name} is (is ffmpeg installed and the file a real video/audio?)")


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def swap_audio(video: Path, audio: Path, out: Path) -> Path:
    """The video's picture with a new audio track, the picture copied untouched."""
    if not shutil.which("ffmpeg"):
        raise Fail("ffmpeg isn't installed, so the new audio can't go back into the video")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)], check=True)
    return out


def audio_only(src: Path) -> tuple[Path, bool]:
    """A video goes up as its audio only: far smaller and faster (a long video doesn't fill memory), the picture never
    leaves, and the result goes back over the original picture untouched (HDR and all). Returns the file to send and
    whether it's a temporary copy to delete."""
    if not is_video(src) or not shutil.which("ffmpeg"):
        return src, False
    tmp = Path(tempfile.mkdtemp(prefix="ken-eleven-")) / (src.stem + ".audio.mp3")
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame",
                        "-b:a", "192k", str(tmp)], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        _drop(tmp, True)
        raise Fail(f"Couldn't take the sound out of {src.name}: is it a video ffmpeg can read, with sound?")
    return tmp, True


def need(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise Fail(f"No file at {p}")
    return p


def cost(feature: str, *, seconds: float = 0, chars: int = 0) -> float:
    unit, usd = PRICE[feature]
    if unit == "per_min":
        return round(usd * seconds / 60, 4)
    if unit == "per_1k_chars":
        return round(usd * chars / 1000, 4)
    return usd


def done(**info) -> dict:
    if host():  # Ken's account: no prices for the chat, only what's left
        info.pop("usd", None)
        if QUOTA_LEFT:
            info["quota_left"] = _readable(QUOTA_LEFT[0])
    elif HOST_NOT_LIVE:
        info["account"] = "own"
        info["account_note"] = "Ken's built-in ElevenLabs isn't live yet, so this ran on their own ElevenLabs key (their credits)."
    print(json.dumps(info, ensure_ascii=False))
    return info


def _readable(left: str) -> str:
    """"540 seconds" → "9 min"; other units as they are."""
    n, _, unit = left.partition(" ")
    if unit == "seconds" and n.isdigit():
        return f"{int(n) / 60:.1f}".rstrip("0").rstrip(".") + " min"
    return left


# The host's feature names, for estimate (tts on Flash and v2 dubs are counted under their own).
QUOTA_FEATURE = {"tts_flash": "tts", "dub_v2": "dub_v2"}


# ---------- captions: Scribe words → SRT ----------

def _ts(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def words_to_srt(words: list[dict], max_chars: int = 32, max_secs: float = 2.5, speakers: bool = False) -> str:
    """Short caption lines for reels: a cue ends at a sentence end, a long pause, max_chars or max_secs."""
    cues, cur = [], []
    speakers = speakers and len({w.get("speaker_id") for w in words if w.get("type", "word") == "word"}) > 1

    def flush():
        if cur:
            text = " ".join(w["text"].strip() for w in cur).strip()
            if speakers and cur[0].get("speaker_id"):
                text = f"[{cur[0]['speaker_id']}] {text}"
            cues.append((cur[0]["start"], cur[-1]["end"], text))
            cur.clear()

    for w in words:
        if w.get("type", "word") != "word" or not w.get("text", "").strip():
            continue
        if cur:
            gap = w["start"] - cur[-1]["end"]
            length = len(" ".join(x["text"] for x in cur)) + 1 + len(w["text"])
            if (gap > 0.6 or length > max_chars or w["end"] - cur[0]["start"] > max_secs
                    or (speakers and w.get("speaker_id") != cur[0].get("speaker_id"))):
                flush()
        cur.append(w)
        if w["text"].rstrip().endswith((".", "?", "!", "।")):
            flush()
    flush()
    return "".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(cues, 1))


# ---------- commands ----------

def cmd_check(a):
    q = call("GET", "/quota", host_only=True) if host() else None
    if q is not None:
        left = {f: _readable(f"{v['left']} {v['unit']}") for f, v in q.get("features", {}).items()}
        return done(account="ken", left=left, resets=q.get("resets"), can_clone=q.get("features", {}).get("clone", {}).get("left", 0) > 0)
    s = call("GET", "/v1/user/subscription")
    left = (s.get("character_limit") or 0) - (s.get("character_count") or 0)
    return done(tier=s.get("tier"), credits_used=s.get("character_count"), credits_limit=s.get("character_limit"),
                credits_left=left, resets_unix=s.get("next_character_count_reset_unix"),
                can_clone=s.get("can_use_instant_voice_cloning"), voice_slots=f"{s.get('voice_slots_used')}/{s.get('voice_limit')}")


def cmd_voices(a):
    r = call("GET", "/v2/voices", query={"page_size": 100, "search": a.search})
    return done(voices=[{"id": v["voice_id"], "name": v.get("name"), "kind": v.get("category"),
                         "labels": v.get("labels")} for v in r.get("voices", [])])


def cmd_estimate(a):
    secs = duration(need(a.file)) if a.file else (a.seconds or 0)
    quota = call("GET", "/quota", host_only=True) if host() else None
    if quota is not None:
        feature = QUOTA_FEATURE.get(a.feature, a.feature)
        q = quota.get("features", {}).get(feature, {})
        unit = q.get("unit", "seconds")
        needs = {"chars": a.chars or 0, "seconds": round(secs), "voices": 1, "runs": 1}.get(unit, 0)
        return done(feature=a.feature, needs=_readable(f"{needs} {unit}"), left=_readable(f"{q.get('left', 0)} {unit}"),
                    fits=needs <= q.get("left", 0))
    usd = cost(a.feature, seconds=secs, chars=a.chars or 0)
    return done(feature=a.feature, seconds=round(secs, 1), chars=a.chars, usd=usd,
                note="Estimate from elevenlabs.io/pricing/api; on a plan it comes out of credits first.")


def cmd_tts(a):
    text = a.text if a.text is not None else need(a.text_file).read_text()
    if not text.strip():
        raise Fail("No text to speak")
    body = {"text": text, "model_id": a.model}
    if a.speed:
        body["voice_settings"] = {"speed": a.speed}
    audio = call("POST", f"/v1/text-to-speech/{a.voice}", query={"output_format": a.format}, json_body=body, raw=True)
    out = Path(a.out)
    out.write_bytes(audio)
    feature = "tts_flash" if "flash" in a.model else "tts"
    return done(out=str(out), chars=len(text), usd=cost(feature, chars=len(text)))


def cmd_clone(a):
    # The consent rule lives here too, not only in the skill text: their yes (said in chat, or a recording), or no clone.
    if not a.own_voice:
        raise Fail("Cloning is only for the person's own voice. Pass --own-voice once they've confirmed it's theirs.")
    said = (a.said or "").strip()
    consent = need(a.consent) if a.consent else None
    if not said and not consent:
        raise Fail("No consent yet. Ask them once in chat: \"this is your own voice and you're OK with me cloning it?\" "
                   "and pass their answer with --said.")
    samples = [need(s) for s in a.sample]
    total = sum(duration(s) for s in samples)
    if total < 10:
        raise Fail(f"Samples add up to {total:.0f}s; ElevenLabs needs at least 10 s (1 to 2 minutes of clean talking is best).")
    how = "chat" if said else "recording"
    note = f"chat: {said}" if said else consent.name
    note = " ".join(note.split()).encode("ascii", "backslashreplace").decode()  # a header is ASCII, one line
    labels = json.dumps({"consent": how, "made_by": "ken"})
    r = call("POST", "/v1/voices/add", fields={"name": a.name, "description": a.description or "Their own voice, cloned with consent",
                                               "remove_background_noise": "true" if a.denoise else "false", "labels": labels},
             files=[("files", s) for s in samples],
             headers={"X-Ken-Consent": "own-voice", "X-Ken-Consent-Note": note[:200]})
    record = Path(a.record) if a.record else samples[0].parent / "consent.json"
    entries = json.loads(record.read_text()) if record.exists() else []
    entries.append({"who": a.who or a.name, "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "how": how, "said": said or None,
                    "recording": str(consent) if consent else None, "samples": [str(s) for s in samples],
                    "voice_id": r.get("voice_id")})
    record.write_text(json.dumps(entries, ensure_ascii=False, indent=1))
    return done(voice_id=r.get("voice_id"), requires_verification=r.get("requires_verification"), sample_seconds=round(total, 1),
                consent=how, record=str(record))


def cmd_changer(a):
    src = need(a.inp)
    if duration(src) > 300:
        raise Fail("Voice changer takes 5 minutes at most per file: cut it into parts first")
    send, tmp = audio_only(src)
    try:
        audio = call("POST", f"/v1/voice-changer/{a.voice}", query={"output_format": "mp3_44100_128"},
                     fields={"model_id": a.model, "remove_background_noise": "true" if a.denoise else "false"},
                     files=[("audio", send)], raw=True, seconds=duration(send))
    finally:
        _drop(send, tmp)
    return _audio_out(src, audio, Path(a.out), "changer")


def cmd_isolate(a):
    src = need(a.inp)
    if src.stat().st_size > 500 * 1024 * 1024:
        raise Fail("Voice isolator takes files up to 500 MB and 1 hour")
    send, tmp = audio_only(src)
    try:
        audio = call("POST", "/v1/audio-isolation", files=[("audio", send)], raw=True, seconds=duration(send))
    finally:
        _drop(send, tmp)
    return _audio_out(src, audio, Path(a.out), "isolate")


def _drop(path: Path, tmp: bool):
    if tmp:
        shutil.rmtree(path.parent, ignore_errors=True)


def _audio_out(src: Path, audio: bytes, out: Path, feature: str):
    secs = duration(src)
    if is_video(src) and is_video(out):
        tmp = out.with_suffix(".audio.mp3")
        tmp.write_bytes(audio)
        swap_audio(src, tmp, out)
        tmp.unlink()
    else:
        out.write_bytes(audio)
    return done(out=str(out), seconds=round(secs, 1), usd=cost(feature, seconds=secs))


def cmd_sfx(a):
    body = {"text": a.text, "model_id": a.model, "prompt_influence": a.influence, "loop": a.loop}
    if a.seconds:
        body["duration_seconds"] = max(0.5, min(30.0, a.seconds))
    audio = call("POST", "/v1/sound-generation", json_body=body, raw=True)
    Path(a.out).write_bytes(audio)
    return done(out=a.out, usd=cost("sfx", seconds=a.seconds or 5))


def cmd_music(a):
    ms = int(max(3, min(300, a.seconds)) * 1000)
    body = {"prompt": a.prompt, "music_length_ms": ms, "model_id": a.model, "force_instrumental": a.instrumental}
    audio = call("POST", "/v1/music", json_body=body, raw=True, timeout=900)
    Path(a.out).write_bytes(audio)
    return done(out=a.out, seconds=ms / 1000, usd=cost("music", seconds=ms / 1000))


def cmd_captions(a):
    src = need(a.inp)
    fields = {"model_id": a.model, "timestamps_granularity": "word", "tag_audio_events": "false",
              "diarize": "true" if a.speakers else "false", "language_code": a.lang}
    send, tmp = audio_only(src)
    try:
        r = call("POST", "/v1/speech-to-text", fields=fields, files=[("file", send)], timeout=1800, seconds=duration(send))
    finally:
        _drop(send, tmp)
    words = r.get("words", [])
    out = Path(a.out)
    out.write_text(words_to_srt(words, max_chars=a.max_chars, speakers=a.speakers))
    words_json = out.with_suffix(".words.json")
    words_json.write_text(json.dumps({"language": r.get("language_code"), "text": r.get("text"),
                                      "words": [w for w in words if w.get("type", "word") == "word"]}, ensure_ascii=False, indent=1))
    secs = r.get("audio_duration_secs") or duration(src)
    return done(out=str(out), words=str(words_json), language=r.get("language_code"), usd=cost("captions", seconds=secs))


def _described(text: str) -> str:
    if len(text.strip()) < 20:
        raise Fail("Describe the voice in at least 20 characters (who, age, accent, pace, mood), like "
                   "\"warm, calm Indian woman in her 30s, slow documentary narrator\".")
    return text


def cmd_design(a):
    body = {"voice_description": _described(a.describe), "model_id": a.model}
    body.update({"text": a.text} if a.text else {"auto_generate_text": True})
    r = call("POST", "/v1/text-to-voice/design", json_body=body)
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    previews = []
    for i, p in enumerate(r.get("previews", []), 1):
        f = d / f"preview-{i}.mp3"
        f.write_bytes(base64.b64decode(p["audio_base_64"]))
        previews.append({"file": str(f), "generated_voice_id": p["generated_voice_id"]})
    return done(previews=previews, text=r.get("text"))


def cmd_design_save(a):
    r = call("POST", "/v1/text-to-voice", json_body={"voice_name": a.name, "voice_description": _described(a.describe),
                                                    "generated_voice_id": a.id})
    return done(voice_id=r.get("voice_id"), name=r.get("name"))


# Dubbing. v1 (default): $0.50/min, gives back the finished video. v2 (--v2): $2.20/min, better voices, gives
# lossless audio + subtitles, which go back over the original picture here. A job id is "v1:<id>" or "v2:<project>:<lang>".

def dub_start(a) -> str:
    src = Path(a.inp) if a.inp else None
    if src:
        need(str(src))
    if not src and not a.url:
        raise Fail("Give --in a file or --url a link")
    # A video goes up as its audio, and the dubbed audio goes back over the original picture (":a").
    send, tmp = audio_only(src) if src else (None, False)
    try:
        return _dub_start(a, send, send != src)
    finally:
        if send:
            _drop(send, tmp)


def _dub_start(a, send: Path | None, audio: bool) -> str:
    src = send
    files = [("file", src)] if src else []
    seconds = duration(src) if src else (getattr(a, "seconds", None) or 60)  # a link: the host corrects it from ElevenLabs
    if a.v2:
        r = call("POST", "/v1/dubbing/project", fields={"source_url": a.url, "source_language": a.src_lang,
                                                       "target_language": a.to, "model_id": "dubbing_v2"}, files=files,
                 seconds=seconds)
        langs = r.get("language_ids") or []
        if not langs:
            lang = call("POST", f"/v1/dubbing/project/{r['project_id']}/language", json_body={"target_language": a.to})
            langs = [lang["language_id"]]
        return f"v2:{r['project_id']}:{langs[0]}"
    r = call("POST", "/v1/dubbing", fields={"source_url": a.url, "source_lang": a.src_lang or "auto", "target_lang": a.to,
                                           "num_speakers": str(a.speakers or 0), "watermark": "false",
                                           "highest_resolution": "true", "name": src.stem.removesuffix(".audio") if src else None}, files=files,
             seconds=seconds)
    return f"v1:{r['dubbing_id']}" + (":a" if audio else "")


def dub_status(job: str) -> dict:
    kind, *ids = job.split(":")
    if kind == "v2":
        lang = call("GET", f"/v1/dubbing/project/{ids[0]}/language/{ids[1]}")
        state = {"completed": "done", "failed": "failed"}.get(lang.get("status"), "working")
        return {"job": job, "state": state, "raw": lang.get("status"), "error": lang.get("error"), "outputs": lang.get("outputs")}
    r = call("GET", f"/v1/dubbing/{ids[0]}")
    state = {"dubbed": "done", "failed": "failed"}.get(r.get("status"), "working")
    return {"job": job, "state": state, "raw": r.get("status"), "error": r.get("error")}


def _subs(out: Path, to: str, ext: str) -> Path:
    """reel.hi.mp4 → reel.hi.srt (not reel.hi.hi.srt); reel.mp4 → reel.hi.srt."""
    return out.with_name(out.stem.removesuffix(f".{to}") + f".{to}.{ext}")


def dub_get(job: str, to: str, out: Path, source: Path | None, status: dict | None = None) -> dict:
    kind, *ids = job.split(":")
    got = {"out": str(out)}
    if kind == "v2":
        outputs = (status or dub_status(job)).get("outputs") or {}
        if not outputs.get("lossless_audio"):
            raise Fail("The dub isn't ready yet")
        flac = download(outputs["lossless_audio"], out.with_suffix(".dub.flac"))
        if outputs.get("subtitles"):
            got["subtitles"] = str(download(outputs["subtitles"], _subs(out, to, "vtt")))
        if source and is_video(source) and is_video(out):
            swap_audio(source, flac, out)
            flac.unlink()
        else:
            flac.replace(out)
        return got
    dubbed = call("GET", f"/v1/dubbing/{ids[0]}/audio/{to}", raw=True, timeout=1800)
    if ids[1:] == ["a"] and source and is_video(source) and is_video(out):
        track = out.with_suffix(".dub.mp3")
        track.write_bytes(dubbed)
        swap_audio(source, track, out)
        track.unlink()
    else:
        out.write_bytes(dubbed)
    try:
        srt = call("GET", f"/v1/dubbing/{ids[0]}/transcripts/{to}/format/srt")
        if srt.get("srt"):
            sub = _subs(out, to, "srt")
            sub.write_text(srt["srt"])
            got["subtitles"] = str(sub)
    except Fail:
        pass  # subtitles are a bonus
    return got


def cmd_dub(a):
    secs = duration(need(a.inp)) if a.inp else 0
    job = dub_start(a)
    waited, status = 0, {}
    while waited <= a.max_wait:
        status = dub_status(job)
        if status["state"] != "working":
            break
        time.sleep(a.poll)
        waited += a.poll
    if status.get("state") == "failed":
        raise Fail(f"The dub failed: {status.get('error') or 'no reason given'} (job {job})")
    if status.get("state") != "done":
        return done(job=job, state="still working", note=f"Run dub-get {job} --to {a.to} --out {a.out}" + (f" --source {a.inp}" if a.inp else "") + " later")
    got = dub_get(job, a.to, Path(a.out), Path(a.inp) if a.inp else None, status)
    return done(job=job, **got, usd=cost("dub_v2" if a.v2 else "dub", seconds=secs))


def cmd_dub_start(a):
    secs = duration(need(a.inp)) if a.inp else 0
    return done(job=dub_start(a), usd=cost("dub_v2" if a.v2 else "dub", seconds=secs))


def cmd_dub_status(a):
    s = dub_status(a.job)
    s.pop("outputs", None)  # signed links: not for the chat
    return done(**s)


def cmd_dub_get(a):
    return done(**dub_get(a.job, a.to, Path(a.out), Path(a.source) if a.source else None))


def _hdr(path: Path) -> bool:
    try:
        tr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer",
                             "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    except OSError:
        return False
    return tr in ("smpte2084", "arib-std-b67")


def cmd_over(a):
    """A new voiceover under their footage: the picture runs (and loops, clip after clip) for as long as the voice
    does, their old sound goes (or stays quietly under with --bed). The result is a normal talking video, so the
    video skill's cut, captions and render take it like any other."""
    if not shutil.which("ffmpeg"):
        raise Fail("ffmpeg isn't installed, so the voiceover can't go under the footage")
    clips, audio, out = [need(v) for v in a.video], need(a.audio), Path(a.out)
    secs = duration(audio)
    if len(clips) > 1 and any(_hdr(c) for c in clips):
        raise Fail("Some of these clips are HDR (phone footage). Put the voiceover under one clip at a time, "
                   "or cut them into one video first with the video skill, so the colours stay right.")
    if a.bed and len(clips) > 1:
        raise Fail("--bed keeps one clip's own sound under the voice; with several clips, leave it out.")
    ff = ["ffmpeg", "-v", "error", "-y"]
    if len(clips) == 1:  # one clip: the picture copied untouched (HDR too), looped if the voice is longer
        ff += ["-stream_loop", "-1", "-ss", str(a.start or 0), "-i", str(clips[0])]
        vmap, vcodec = "0:v:0", ["-c:v", "copy"]
    else:  # several: each fitted to the first one's frame, one after another, round again if the voice is longer
        w, h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", str(clips[0])], capture_output=True, text=True).stdout.strip().split(",")[:2]
        order, total = [], 0.0
        while total < secs:
            for c in clips:
                order.append(c); total += duration(c)
        for c in order:
            ff += ["-i", str(c)]
        fit = "".join(f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,setsar=1,format=yuv420p[v{i}];"
                      for i in range(len(order)))
        fit += "".join(f"[v{i}]" for i in range(len(order))) + f"concat=n={len(order)}:v=1:a=0[v]"
        ff += ["-filter_complex", fit]
        vmap, vcodec = "[v]", ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]
    n = ff.count("-i")  # the voiceover's input number
    ff += ["-i", str(audio)]
    amap = f"{n}:a:0"
    if a.bed:
        ff += ["-filter_complex", f"[0:a:0]volume={a.bed}[b];[{n}:a:0][b]amix=inputs=2:duration=first:normalize=0[a]"]
        amap = "[a]"
    ff += ["-map", vmap, "-map", amap, *vcodec, "-c:a", "aac", "-b:a", "192k", "-t", f"{secs:.3f}", str(out)]
    try:
        subprocess.run(ff, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise Fail(f"Couldn't put the voiceover under the footage: {e.stderr.decode(errors='replace').strip().splitlines()[-1:]}")
    return done(out=str(out), seconds=round(secs, 1), clips=len(clips))


def cmd_voice_delete(a):
    call("DELETE", f"/v1/voices/{a.id}")
    return done(deleted=a.id)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eleven.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check").set_defaults(fn=cmd_check)
    s = sub.add_parser("voices"); s.add_argument("--search"); s.set_defaults(fn=cmd_voices)
    s = sub.add_parser("estimate"); s.add_argument("feature", choices=sorted(PRICE)); s.add_argument("file", nargs="?")
    s.add_argument("--seconds", type=float); s.add_argument("--chars", type=int); s.set_defaults(fn=cmd_estimate)

    s = sub.add_parser("tts"); s.add_argument("--voice", required=True); g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--text"); g.add_argument("--text-file"); s.add_argument("--out", required=True)
    s.add_argument("--model", default="eleven_multilingual_v2"); s.add_argument("--format", default="mp3_44100_128")
    s.add_argument("--speed", type=float); s.set_defaults(fn=cmd_tts)

    s = sub.add_parser("clone"); s.add_argument("--name", required=True); s.add_argument("--sample", action="append", required=True)
    s.add_argument("--said", help="their yes in chat, in their words (it's their own voice and they're OK with a clone)")
    s.add_argument("--consent", help="or a consent recording / note file")
    s.add_argument("--who", help="who said yes (default: --name)")
    s.add_argument("--record", help="the consent record to add to (default: consent.json next to the first sample)")
    s.add_argument("--own-voice", action="store_true", help="they confirmed every sample is their own voice")
    s.add_argument("--description"); s.add_argument("--denoise", action="store_true"); s.set_defaults(fn=cmd_clone)

    s = sub.add_parser("changer"); s.add_argument("--voice", required=True); s.add_argument("--in", dest="inp", required=True)
    s.add_argument("--out", required=True); s.add_argument("--model", default="eleven_multilingual_sts_v2")
    s.add_argument("--denoise", action="store_true"); s.set_defaults(fn=cmd_changer)

    s = sub.add_parser("isolate"); s.add_argument("--in", dest="inp", required=True); s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_isolate)

    s = sub.add_parser("sfx"); s.add_argument("--text", required=True); s.add_argument("--seconds", type=float)
    s.add_argument("--influence", type=float, default=0.3); s.add_argument("--loop", action="store_true")
    s.add_argument("--model", default="eleven_text_to_sound_v3"); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_sfx)

    s = sub.add_parser("music"); s.add_argument("--prompt", required=True); s.add_argument("--seconds", type=float, default=30)
    s.add_argument("--instrumental", action="store_true"); s.add_argument("--model", default="music_v2_5")
    s.add_argument("--out", required=True); s.set_defaults(fn=cmd_music)

    s = sub.add_parser("captions"); s.add_argument("--in", dest="inp", required=True); s.add_argument("--out", required=True)
    s.add_argument("--lang"); s.add_argument("--speakers", action="store_true"); s.add_argument("--max-chars", type=int, default=32)
    s.add_argument("--model", default="scribe_v2"); s.set_defaults(fn=cmd_captions)

    s = sub.add_parser("design"); s.add_argument("--describe", required=True); s.add_argument("--text")
    s.add_argument("--model", default="eleven_multilingual_ttv_v2"); s.add_argument("--out-dir", required=True)
    s.set_defaults(fn=cmd_design)
    s = sub.add_parser("design-save"); s.add_argument("--id", required=True); s.add_argument("--name", required=True)
    s.add_argument("--describe", required=True); s.set_defaults(fn=cmd_design_save)

    for name, fn in (("dub", cmd_dub), ("dub-start", cmd_dub_start)):
        s = sub.add_parser(name); s.add_argument("--in", dest="inp"); s.add_argument("--url"); s.add_argument("--to", required=True)
        s.add_argument("--from", dest="src_lang"); s.add_argument("--speakers", type=int); s.add_argument("--v2", action="store_true")
        s.add_argument("--seconds", type=float, help="how long a --url video is, if known")
        if name == "dub":
            s.add_argument("--out", required=True); s.add_argument("--poll", type=int, default=15)
            s.add_argument("--max-wait", type=int, default=3000)
        s.set_defaults(fn=fn)
    s = sub.add_parser("dub-status"); s.add_argument("job"); s.set_defaults(fn=cmd_dub_status)
    s = sub.add_parser("dub-get"); s.add_argument("job"); s.add_argument("--to", required=True); s.add_argument("--out", required=True)
    s.add_argument("--source", help="the original video (v2 dubs, and v1 dubs made on Ken's account)"); s.set_defaults(fn=cmd_dub_get)
    s = sub.add_parser("voice-delete"); s.add_argument("id"); s.set_defaults(fn=cmd_voice_delete)
    s = sub.add_parser("over", help="a voiceover under their footage (no ElevenLabs call)")
    s.add_argument("--video", action="append", required=True); s.add_argument("--audio", required=True)
    s.add_argument("--out", required=True); s.add_argument("--start", type=float, help="seconds into a single clip")
    s.add_argument("--bed", type=float, help="keep the footage's own sound under the voice at this volume (0.1 = quiet)")
    s.set_defaults(fn=cmd_over)
    return p


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        a.fn(a)
        return 0
    except Fail as e:
        print(_redact(e.msg), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
