#!/usr/bin/env python3
"""ElevenLabs for the video kit: one small command per feature, Python standard library only (no installs).

The key is read from ELEVENLABS_API_KEY and never printed. Every command prints one JSON line on success
(the files it wrote, the job id, the estimated cost) so the chat can read it back; errors go to stderr.

    eleven.py check                                      plan, credits left, whether voice cloning is on
    eleven.py voices [--search calm]                     their voices (id, name, kind)
    eleven.py estimate <feature> [file] [--chars N]      what a run would cost, before spending anything
    eleven.py tts --voice ID (--text "..." | --text-file f) --out vo.mp3 [--model eleven_multilingual_v2]
    eleven.py clone --name "Swap" --sample a.mp3 [--sample b.mp3] --consent consent.mp3 --own-voice
    eleven.py changer --voice ID --in take.mp3 --out new.mp3
    eleven.py isolate --in noisy.mp4 --out clean.mp4     a video gets its cleaned audio put back in
    eleven.py sfx --text "door slam" [--seconds 2] --out door.mp3
    eleven.py music --prompt "lofi beat" --seconds 30 --out bed.mp3 [--instrumental]
    eleven.py captions --in video.mp4 --out video.srt [--lang hi] [--speakers]
    eleven.py design --describe "warm, older Indian man" [--text "..."] --out-dir previews/
    eleven.py design-save --id GENERATED_ID --name "Narrator" --describe "..."
    eleven.py dub --in reel.mp4 --to hi [--from en] --out reel.hi.mp4 [--v2] [--speakers 1]
    eleven.py dub-start / dub-status ID / dub-get ID --to hi --out f    the same, in steps (a long video)
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
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API = os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io")
KEY_HELP = ("No ElevenLabs key. Ask them to add it in the Brain tab, under Accounts & passwords, "
            "named ELEVENLABS_API_KEY (elevenlabs.io → Developers → API keys).")
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


def _redact(text: str) -> str:
    k = os.environ.get("ELEVENLABS_API_KEY", "")
    return text.replace(k, "[key]") if k else text


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
         fields: dict | None = None, files: list | None = None, raw: bool = False, timeout: int = 600):
    """One API call. Returns parsed JSON, or bytes when raw=True. Errors become a readable Fail."""
    url = API + path + ("?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None}) if query else "")
    headers = {"xi-api-key": key(), "User-Agent": "ken-video-kit/1"}
    data = None
    if json_body is not None:
        data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
    elif fields is not None or files:
        data, headers["Content-Type"] = multipart(fields or {}, files or [])
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise Fail(_explain(e.code, _redact(detail)))
    except urllib.error.URLError as e:
        raise Fail(f"Couldn't reach ElevenLabs: {e.reason}")
    return body if raw else json.loads(body or b"{}")


def _explain(code: int, detail: str) -> str:
    hint = {401: "the key is wrong or was revoked: ask them to check ELEVENLABS_API_KEY in Brain",
            402: "out of credits or the plan doesn't include this",
            403: "the plan doesn't include this feature (voice cloning and music need a paid plan)",
            413: "the file is too big for this feature",
            429: "too many jobs at once or rate limited: wait a minute, don't retry in a loop"}.get(code, "")
    return f"ElevenLabs said {code}" + (f" ({hint})" if hint else "") + f": {detail}"


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
    print(json.dumps(info, ensure_ascii=False))
    return info


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
    # The consent rule lives here too, not only in the skill text: no consent recording, no clone.
    if not a.own_voice:
        raise Fail("Cloning is only for the person's own voice. Pass --own-voice once they've confirmed it's theirs.")
    consent = need(a.consent)
    samples = [need(s) for s in a.sample]
    total = sum(duration(s) for s in samples)
    if total < 10:
        raise Fail(f"Samples add up to {total:.0f}s; ElevenLabs needs at least 10 s (1 to 2 minutes of clean talking is best).")
    labels = json.dumps({"consent_recording": consent.name, "made_by": "ken"})
    r = call("POST", "/v1/voices/add", fields={"name": a.name, "description": a.description or "Their own voice, cloned with consent",
                                               "remove_background_noise": "true" if a.denoise else "false", "labels": labels},
             files=[("files", s) for s in samples])
    return done(voice_id=r.get("voice_id"), requires_verification=r.get("requires_verification"), sample_seconds=round(total, 1),
                consent=str(consent))


def cmd_changer(a):
    src = need(a.inp)
    if duration(src) > 300:
        raise Fail("Voice changer takes 5 minutes at most per file: cut it into parts first")
    audio = call("POST", f"/v1/voice-changer/{a.voice}", query={"output_format": "mp3_44100_128"},
                 fields={"model_id": a.model, "remove_background_noise": "true" if a.denoise else "false"},
                 files=[("audio", src)], raw=True)
    return _audio_out(src, audio, Path(a.out), "changer")


def cmd_isolate(a):
    src = need(a.inp)
    if src.stat().st_size > 500 * 1024 * 1024:
        raise Fail("Voice isolator takes files up to 500 MB and 1 hour")
    audio = call("POST", "/v1/audio-isolation", files=[("audio", src)], raw=True)
    return _audio_out(src, audio, Path(a.out), "isolate")


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
    r = call("POST", "/v1/speech-to-text", fields=fields, files=[("file", src)], timeout=1800)
    words = r.get("words", [])
    out = Path(a.out)
    out.write_text(words_to_srt(words, max_chars=a.max_chars, speakers=a.speakers))
    words_json = out.with_suffix(".words.json")
    words_json.write_text(json.dumps({"language": r.get("language_code"), "text": r.get("text"),
                                      "words": [w for w in words if w.get("type", "word") == "word"]}, ensure_ascii=False, indent=1))
    secs = r.get("audio_duration_secs") or duration(src)
    return done(out=str(out), words=str(words_json), language=r.get("language_code"), usd=cost("captions", seconds=secs))


def cmd_design(a):
    body = {"voice_description": a.describe, "model_id": a.model}
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
    r = call("POST", "/v1/text-to-voice", json_body={"voice_name": a.name, "voice_description": a.describe,
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
    files = [("file", src)] if src else []
    if a.v2:
        r = call("POST", "/v1/dubbing/project", fields={"source_url": a.url, "source_language": a.src_lang,
                                                       "target_language": a.to, "model_id": "dubbing_v2"}, files=files)
        langs = r.get("language_ids") or []
        if not langs:
            lang = call("POST", f"/v1/dubbing/project/{r['project_id']}/language", json_body={"target_language": a.to})
            langs = [lang["language_id"]]
        return f"v2:{r['project_id']}:{langs[0]}"
    r = call("POST", "/v1/dubbing", fields={"source_url": a.url, "source_lang": a.src_lang or "auto", "target_lang": a.to,
                                           "num_speakers": str(a.speakers or 0), "watermark": "false",
                                           "highest_resolution": "true", "name": src.stem if src else None}, files=files)
    return f"v1:{r['dubbing_id']}"


def dub_status(job: str) -> dict:
    kind, *ids = job.split(":")
    if kind == "v2":
        lang = call("GET", f"/v1/dubbing/project/{ids[0]}/language/{ids[1]}")
        state = {"completed": "done", "failed": "failed"}.get(lang.get("status"), "working")
        return {"job": job, "state": state, "raw": lang.get("status"), "error": lang.get("error"), "outputs": lang.get("outputs")}
    r = call("GET", f"/v1/dubbing/{ids[0]}")
    state = {"dubbed": "done", "failed": "failed"}.get(r.get("status"), "working")
    return {"job": job, "state": state, "raw": r.get("status"), "error": r.get("error")}


def dub_get(job: str, to: str, out: Path, source: Path | None, status: dict | None = None) -> dict:
    kind, *ids = job.split(":")
    got = {"out": str(out)}
    if kind == "v2":
        outputs = (status or dub_status(job)).get("outputs") or {}
        if not outputs.get("lossless_audio"):
            raise Fail("The dub isn't ready yet")
        flac = download(outputs["lossless_audio"], out.with_suffix(".dub.flac"))
        if outputs.get("subtitles"):
            got["subtitles"] = str(download(outputs["subtitles"], out.with_suffix(f".{to}.vtt")))
        if source and is_video(source) and is_video(out):
            swap_audio(source, flac, out)
            flac.unlink()
        else:
            flac.replace(out)
        return got
    out.write_bytes(call("GET", f"/v1/dubbing/{ids[0]}/audio/{to}", raw=True, timeout=1800))
    try:
        srt = call("GET", f"/v1/dubbing/{ids[0]}/transcripts/{to}/format/srt")
        if srt.get("srt"):
            sub = out.with_suffix(f".{to}.srt")
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
        return done(job=job, state="still working", note=f"Run dub-get {job} --to {a.to} --out {a.out} later")
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
    s.add_argument("--consent", required=True, help="their recorded consent line")
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
        if name == "dub":
            s.add_argument("--out", required=True); s.add_argument("--poll", type=int, default=15)
            s.add_argument("--max-wait", type=int, default=3000)
        s.set_defaults(fn=fn)
    s = sub.add_parser("dub-status"); s.add_argument("job"); s.set_defaults(fn=cmd_dub_status)
    s = sub.add_parser("dub-get"); s.add_argument("job"); s.add_argument("--to", required=True); s.add_argument("--out", required=True)
    s.add_argument("--source", help="the original video, for v2 dubs"); s.set_defaults(fn=cmd_dub_get)
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
