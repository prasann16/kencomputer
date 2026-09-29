"""Desktop process boundary for the shared transcriber.

A failed native decoder must not strand the chat or future voice notes.
"""
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

log = logging.getLogger('ken.desktop.voice')


class VoiceWorker:
    def __init__(self, startup_timeout=60, response_timeout=None):
        self.process = None
        self.lock = threading.Lock()
        self.startup_timeout = startup_timeout
        self.response_timeout = response_timeout

    def _read(self, timeout):
        try:
            line = self.output.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError('Voice transcription took too long. Your recording is kept; try again.') from None
        if not line:
            raise RuntimeError('Voice transcription stopped. Your recording is kept; try again.')
        result = json.loads(line)
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        command = [sys.executable, '--voice-worker'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'desktop.voice']
        self.output = queue.Queue()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=None, text=True, bufsize=1,
                                        cwd=Path(__file__).resolve().parent.parent if not getattr(sys, 'frozen', False) else None,
                                        env={**os.environ, 'PYTHONUNBUFFERED': '1'})
        def read_lines(pipe, output):
            try:
                for line in pipe:
                    output.put(line)
            finally:
                output.put('')
                pipe.close()
        threading.Thread(target=read_lines, args=(self.process.stdout, self.output), daemon=True).start()
        if not self._read(self.startup_timeout).get('ready'):
            raise RuntimeError('Voice could not start. Try again.')

    def _close(self):
        process, self.process = self.process, None
        if process:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def close(self):
        with self.lock:
            self._close()

    def warmup(self):
        with self.lock:
            try:
                self._start()
            except Exception:
                self._close()
                log.exception('Voice preload failed; the next recording will retry')

    def transcribe(self, path):
        # Reject *digital silence*, not quiet speech. Do this before starting a
        # model request so a dead microphone cannot hallucinate a user command.
        duration = recording_duration(path)
        with self.lock:
            try:
                self._start()
                timeout = self.response_timeout if self.response_timeout is not None else min(600, max(30, duration * 3))
                self.process.stdin.write(json.dumps({'path': path}) + '\n')
                self.process.stdin.flush()
                return self._read(timeout)['text']
            except Exception:
                self._close()
                raise


def recording_duration(path):
    import av
    import numpy as np
    samples = 0
    signal = False
    duration = 0.0
    with av.open(path) as audio:
        for frame in audio.decode(audio=0):
            values = frame.to_ndarray()
            samples += values.size
            signal = signal or bool(np.any(values != 0))
            duration += frame.samples / frame.sample_rate
    if not samples or not signal:
        raise RuntimeError('No microphone audio was recorded. Check your microphone and record again.')
    return duration


def run_worker():
    from voice import get_whisper, transcribe
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    get_whisper()
    print(json.dumps({'ready': True}), flush=True)
    for line in sys.stdin:
        try:
            text = transcribe(json.loads(line)['path'])
            result = {'text': text}
        except Exception:
            log.exception('Transcription failed')
            result = {'error': 'Could not transcribe this recording. Try again.'}
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    run_worker()
