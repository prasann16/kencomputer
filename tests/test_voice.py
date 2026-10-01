"""Shared Telegram/desktop transcription without network or model downloads."""
from types import SimpleNamespace
import sys

import voice


def test_cached_model_reused_and_quiet_speech_preserved(monkeypatch):
    loads, calls = [], []

    class Model:
        def __init__(self, name, **kwargs):
            loads.append((name, kwargs))

        def transcribe(self, path, **kwargs):
            calls.append((path, kwargs))
            return iter([SimpleNamespace(text=' quiet speech ', start=0, end=2)]), SimpleNamespace(duration=3)

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(voice, '_model', None)
    monkeypatch.setenv('WHISPER_MODEL', 'small')
    voice.get_whisper()  # startup preload
    assert voice.transcribe('recording.webm') == 'quiet speech'
    assert voice.transcribe('attachment.m4a') == 'quiet speech'
    assert loads == [('small', dict(device='cpu', compute_type='int8', local_files_only=True))]
    assert calls == [('recording.webm', {'vad_filter': False}), ('attachment.m4a', {'vad_filter': False})]


def test_download_only_when_cache_missing_and_honor_configuration(monkeypatch):
    loads = []

    def model(name, **kwargs):
        loads.append((name, kwargs))
        if kwargs.get('local_files_only'):
            raise FileNotFoundError('not installed')
        return object()

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=model))
    monkeypatch.setattr(voice, '_model', None)
    monkeypatch.setenv('WHISPER_MODEL', 'base.en')
    voice.get_whisper()
    assert loads == [('base.en', dict(device='cpu', compute_type='int8', local_files_only=True)),
                     ('base.en', dict(device='cpu', compute_type='int8'))]
