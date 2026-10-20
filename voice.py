"""Shared local speech transcription for Telegram and desktop."""
import logging
import os
import threading
import time

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


def transcribe(path: str) -> str:
    # Keep Telegram's no-VAD behavior: filtering discarded quiet speech.
    with _lock:
        started = time.monotonic()
        model = get_whisper()
        # Segments decode lazily, so the language guess is free to check before any real work.
        segments, info = model.transcribe(path, vad_filter=False, condition_on_previous_text=False)
        if info.language != "en" and info.language_probability < SURE:
            # A short or accented note can read as Norwegian at 31%; decoding as that came back
            # empty after 78s. An unsure guess means English (or KEN_VOICE_LANGUAGE).
            log.info("unsure language guess %s (%.2f); transcribing as %s", info.language, info.language_probability, FALLBACK)
            segments, info = model.transcribe(path, vad_filter=False, condition_on_previous_text=False, language=FALLBACK)
        segments = list(segments)
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
