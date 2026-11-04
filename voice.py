"""Shared local speech transcription for Telegram and desktop."""
import json
import logging
import os
import threading
import time
from pathlib import Path

import crash

log = logging.getLogger("ken.voice")
_model = None
_lock = threading.RLock()
SURE = 0.6  # below this the language guess is noise
FALLBACK = os.environ.get("KEN_VOICE_LANGUAGE", "en")


def get_whisper():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            name = os.environ.get("WHISPER_MODEL", "small")
            log.info("loading whisper model %s ...", name)
            # Reuse the installed model without a network revision check.
            try:
                _model = WhisperModel(name, device="cpu", compute_type="int8", local_files_only=True)
            except FileNotFoundError:
                _model = WhisperModel(name, device="cpu", compute_type="int8")
        return _model


def ready() -> bool:
    return _model is not None


# ---------------------------------------------------------------- in the cloud
# A Ken on a computer in the cloud has no local model (it needs ~1.5 GB of memory). There, the host transcribes:
# Ken sends the audio to KEN_TRANSCRIBE_URL (by default the host's /box/transcribe, next to its Ken AI credits),
# which runs Whisper and answers with Whisper's own segments and word timings. Same results, no model here.
CHUNK_SECONDS = 600  # long recordings go up in 10-minute parts


def cloud_url() -> str:
    url = os.environ.get("KEN_TRANSCRIBE_URL", "").strip()
    if not url:
        ai = os.environ.get("KEN_INCLUDED_AI_URL", "").strip().rstrip("/")
        if ai.startswith("https://") and ai.endswith("/ai"):
            url = ai[: -len("/ai")] + "/box/transcribe"
    return url if url.startswith("https://") else ""


def cloud_token() -> str:
    return (os.environ.get("KEN_TRANSCRIBE_TOKEN") or os.environ.get("KEN_INCLUDED_AI_TOKEN") or "").strip()


def cloud() -> bool:
    """Transcribe through the host: the host offers it, and there's no local model."""
    if not (cloud_url() and cloud_token()):
        return False  # most Kens (a Mac, a server): no host, decided without looking for a model
    from onboarding import voice_installed
    try:
        return not voice_installed()
    except (ImportError, ValueError):
        return True


def _post_audio(path: Path, language: str | None) -> dict:
    import urllib.error
    import urllib.request
    url = cloud_url() + (f"?language={language}" if language else "")
    request = urllib.request.Request(url, data=path.read_bytes(), method="POST", headers={
        # A named client: Cloudflare in front of the host refuses Python's default "Python-urllib" (403).
        "authorization": f"Bearer {cloud_token()}", "content-type": "audio/mpeg", "user-agent": "Ken"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read()).get("error", "")
        except Exception:
            pass
        log.warning("cloud transcription refused: %s %s", exc.code, detail)
        raise RuntimeError(detail or "Transcription isn't available right now. Try again in a minute.") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        log.warning("cloud transcription unreachable: %s", exc)
        raise RuntimeError("Couldn't reach the transcription service. Try again in a minute.") from None


def _cloud_segments(path: str):
    import shutil
    import subprocess
    import tempfile
    from types import SimpleNamespace as NS

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg isn't installed on this computer.")
    with tempfile.TemporaryDirectory(prefix="ken-voice-") as tmp:
        # Mono 16 kHz MP3 in 10-minute parts: small to send, all Whisper needs.
        cmd = [ffmpeg, "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
               "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-reset_timestamps", "1", str(Path(tmp) / "part%03d.mp3")]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        parts = sorted(Path(tmp).glob("part*.mp3"))
        if done.returncode != 0 or not parts:
            raise RuntimeError("Couldn't read the audio in this file.")
        segments, offset, language, probability = [], 0.0, "", 1.0
        for part in parts:
            out = _post_audio(part, None)
            info = out.get("transcription_info") or {}
            if info.get("language") not in (None, "", "en") and (info.get("language_probability") or 0) < SURE:
                log.info("unsure language guess %s; transcribing as %s", info.get("language"), FALLBACK)
                out = _post_audio(part, FALLBACK)
                info = out.get("transcription_info") or {}
            language = language or info.get("language", "")
            probability = min(probability, info.get("language_probability") or 1.0)
            for s in out.get("segments") or []:
                words = [NS(word=w.get("word", ""), start=offset + float(w.get("start", 0)), end=offset + float(w.get("end", 0)))
                         for w in s.get("words") or []]
                segments.append(NS(start=offset + float(s.get("start", 0)), end=offset + float(s.get("end", 0)),
                                   text=s.get("text", ""), words=words))
            offset += float(info.get("duration") or CHUNK_SECONDS)
        return segments, NS(duration=offset, language=language, language_probability=probability)


def _segments(path: str, words: bool = False):
    if cloud():
        return _cloud_segments(path)
    # Keep Telegram's no-VAD behavior: filtering discarded quiet speech.
    model = get_whisper()
    # Segments decode lazily, so the language guess is free to check before any real work.
    segments, info = model.transcribe(path, vad_filter=False, condition_on_previous_text=False, word_timestamps=words)
    if info.language != "en" and info.language_probability < SURE:
        # A short or accented note can read as Norwegian at 31%; decoding as that came back
        # empty after 78s. An unsure guess means English (or KEN_VOICE_LANGUAGE).
        log.info("unsure language guess %s (%.2f); transcribing as %s", info.language, info.language_probability, FALLBACK)
        segments, info = model.transcribe(path, vad_filter=False, condition_on_previous_text=False, language=FALLBACK,
                                          word_timestamps=words)
    return list(segments), info


def transcribe(path: str) -> str:
    with _lock:
        started = time.monotonic()
        segments, info = _segments(path)
        text = " ".join(s.text.strip() for s in segments).strip()
        crash.track("voice_note", audio_seconds=round(info.duration, 1), took_seconds=round(time.monotonic() - started, 1),
                    empty=not text, language=getattr(info, "language", ""))
        log.info("transcribed %.1fs audio in %.2fs -> %d segments, %.1fs speech, %d chars",
                 info.duration, time.monotonic() - started, len(segments), sum(s.end - s.start for s in segments), len(text))
        return text


async def warmup():
    import asyncio
    try:
        await asyncio.to_thread(get_whisper)
    except Exception:
        log.exception("Voice preload failed; the next voice note will retry")


def _stamp(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def captions(path: str) -> str:
    """A timed transcript of a video or audio file, with subtitles (.srt) and every word's timing
    (.words.json, [{"w", "s", "e"}] in seconds, for word-by-word captions) saved next to it."""
    src = Path(path).expanduser()
    if not src.is_file():
        raise ValueError(f"No file at {src}.")
    with _lock:
        started = time.monotonic()
        segments, info = _segments(str(src), words=True)
    crash.track("captions", audio_seconds=round(info.duration), took_seconds=round(time.monotonic() - started))
    srt = src.with_suffix(".srt")
    srt.write_text("\n".join(f"{i}\n{_stamp(s.start)} --> {_stamp(s.end)}\n{s.text.strip()}\n" for i, s in enumerate(segments, 1)))
    timed = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)}
             for s in segments for w in (s.words or []) if w.word.strip()]
    words = src.with_suffix(".words.json")
    words.write_text(json.dumps(timed))
    lines = "\n".join(f"[{_stamp(s.start)[:8]}–{_stamp(s.end)[:8]}] {s.text.strip()}" for s in segments)
    return f"{src.name}: {info.duration / 60:.1f} minutes. Subtitles saved to {srt}, each word's timing to {words}.\n\n{lines}"[:80000]
