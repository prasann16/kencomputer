import json
import subprocess
import sys
import wave

import pytest
from desktop.voice import VoiceWorker


def test_stalled_worker_is_killed_and_next_note_works(monkeypatch, tmp_path):
    path = tmp_path / 'voice.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b'\x01\x00' * 1600)
    real_popen = subprocess.Popen
    processes = []

    def fake_worker(command, **kwargs):
        behavior = 'time.sleep(60)' if not processes else 'print(json.dumps({"text":"hello Ken"}),flush=True)'
        script = 'import sys,json,time\nprint(json.dumps({"ready":True}),flush=True)\nfor line in sys.stdin:\n ' + behavior
        process = real_popen([sys.executable, '-u', '-c', script], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, 'Popen', fake_worker)
    worker = VoiceWorker(startup_timeout=5, response_timeout=.1)
    try:
        with pytest.raises(TimeoutError, match='recording is kept'):
            worker.transcribe(str(path))
        assert processes[0].poll() is not None
        assert path.exists()
        assert worker.transcribe(str(path)) == 'hello Ken'
        assert len(processes) == 2
    finally:
        worker.close()
    assert all(p.poll() is not None for p in processes)


def test_digital_silence_never_reaches_whisper(monkeypatch, tmp_path):
    from desktop.voice import recording_duration
    path = tmp_path / 'muted.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b'\0' * 32000)
    worker = VoiceWorker()
    monkeypatch.setattr(worker, '_start', lambda: pytest.fail('Whisper must not run on digital silence'))
    with pytest.raises(RuntimeError, match='No microphone audio'):
        worker.transcribe(str(path))


def test_extremely_quiet_audio_is_not_discarded(tmp_path):
    from desktop.voice import recording_duration
    path = tmp_path / 'quiet.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b'\x01\x00' * 16000)
    assert recording_duration(str(path)) == pytest.approx(1)
