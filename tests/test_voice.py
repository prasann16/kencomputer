"""Shared Telegram/desktop transcription without network or model downloads."""
from types import SimpleNamespace
import pytest
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
    opts = {'vad_filter': False, 'condition_on_previous_text': False, 'word_timestamps': False}
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



def test_captions_write_subtitles_next_to_the_file(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    import voice

    video = tmp_path / "podcast.mp4"
    video.write_bytes(b"")
    segs = [NS(start=0.0, end=3.58, text=" This week we chased every late invoice.", words=[NS(word=" This", start=0.0, end=0.31)]),
            NS(start=3725.5, end=3727.0, text=" Bye.", words=[NS(word=" Bye.", start=3725.5, end=3726.004)])]
    monkeypatch.setattr(voice, "_segments", lambda path, words=False: (segs, NS(duration=3727.0)))
    out = voice.captions(str(video))
    assert (tmp_path / "podcast.srt").read_text().startswith("1\n00:00:00,000 --> 00:00:03,580\nThis week we chased every late invoice.\n")
    assert "01:02:05,500 --> 01:02:07,000" in (tmp_path / "podcast.srt").read_text()
    assert "62.1 minutes" in out and "[01:02:05–01:02:07] Bye." in out
    import json
    assert json.loads((tmp_path / "podcast.words.json").read_text()) == [{"w": "This", "s": 0.0, "e": 0.31}, {"w": "Bye.", "s": 3725.5, "e": 3726.0}]
    with pytest.raises(ValueError, match="No file"):
        voice.captions(str(tmp_path / "missing.mp4"))


def test_caption_words_split_by_whisper_join_back(tmp_path, monkeypatch):
    """ "24-7", "follow-up", "kencomputer.dev": Whisper hands back pieces, and captions showed "24 -7"."""
    import json
    from types import SimpleNamespace as NS

    import voice

    video = tmp_path / "launch.mp4"
    video.write_bytes(b"")
    said = [(" on", 0.0, 0.2), (" 24", 0.2, 0.5), ("-7.", 0.5, 0.9), (" follow", 1.0, 1.3), ("-up", 1.3, 1.5),
            (" go", 2.0, 2.2), (" to", 2.2, 2.3), (" kencomputer", 2.3, 2.9), (".dev", 2.9, 3.3), (" it", 3.4, 3.5), ("'s", 3.5, 3.6),
            (" 50", 3.7, 3.9), ("%", 3.9, 4.0), (" off.", 4.0, 4.3)]
    segs = [NS(start=0.0, end=4.3, text="".join(w for w, _, _ in said), words=[NS(word=w, start=s, end=e) for w, s, e in said]),
            NS(start=5.0, end=6.0, text=" ...and then.", words=[NS(word="...and", start=5.0, end=5.4), NS(word=" then.", start=5.4, end=6.0)])]
    monkeypatch.setattr(voice, "_segments", lambda path, words=False: (segs, NS(duration=6.0)))
    voice.captions(str(video))
    words = json.loads((tmp_path / "launch.words.json").read_text())
    assert [w["w"] for w in words] == ["on", "24-7.", "follow-up", "go", "to", "kencomputer.dev", "it's", "50%", "off.", "...and", "then."]
    assert words[1] == {"w": "24-7.", "s": 0.2, "e": 0.9}
