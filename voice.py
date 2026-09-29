"""Shared local speech transcription for Telegram and desktop."""
import logging
import os
import threading
import time

log = logging.getLogger("ken.voice")
_model = None
_lock = threading.RLock()


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


def transcribe(path: str) -> str:
    # Keep Telegram's no-VAD behavior: filtering discarded quiet speech.
    with _lock:
        started = time.monotonic()
        segments, info = get_whisper().transcribe(path, vad_filter=False)
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("transcribed %.1fs audio in %.2fs -> %d segments, %.1fs speech, %d chars",
                 info.duration, time.monotonic() - started, len(segments), sum(s.end - s.start for s in segments), len(text))
        return text


async def warmup():
    import asyncio
    try:
        await asyncio.to_thread(get_whisper)
    except Exception:
        log.exception("Voice preload failed; the next voice note will retry")
