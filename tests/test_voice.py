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
            return iter([SimpleNamespace(text=' quiet speech ', start=0, end=2)]), SimpleNamespace(duration=3, language='en', language_probability=0.98)

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(voice, '_model', None)
    monkeypatch.setenv('WHISPER_MODEL', 'small')
    voice.get_whisper()  # startup preload
    assert voice.transcribe('recording.webm') == 'quiet speech'
    assert voice.transcribe('attachment.m4a') == 'quiet speech'
    assert loads == [('small', dict(device='cpu', compute_type='int8', local_files_only=True))]
    opts = {'vad_filter': False, 'condition_on_previous_text': False}
    assert calls == [('recording.webm', opts), ('attachment.m4a', opts)]


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


def test_an_unsure_language_guess_is_transcribed_as_english(monkeypatch):
    calls = []

    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs.get('language'))
            if kwargs.get('language') == 'en':
                return iter([SimpleNamespace(text=' read the previous voice note ', start=0, end=3)]), SimpleNamespace(duration=4, language='en', language_probability=1.0)
            return iter([]), SimpleNamespace(duration=4, language='nn', language_probability=0.31)

    monkeypatch.setattr(voice, '_model', Model())
    assert voice.transcribe('note.oga') == 'read the previous voice note'
    assert calls == [None, 'en']


def test_a_confident_other_language_is_kept(monkeypatch):
    calls = []

    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs.get('language'))
            return iter([SimpleNamespace(text=' olá ', start=0, end=1)]), SimpleNamespace(duration=1, language='pt', language_probability=0.93)

    monkeypatch.setattr(voice, '_model', Model())
    assert voice.transcribe('note.oga') == 'olá'
    assert calls == [None]

