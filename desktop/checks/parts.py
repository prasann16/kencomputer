"""Load every native part Ken depends on and decode a short recording the way a voice
note is decoded. Runs on a plain Python and inside the bundled engine (`ken-engine -c`)."""
import ssl

import aiohttp  # noqa: F401
import av
import claude_agent_sdk  # noqa: F401
import cryptography.hazmat.bindings._rust  # noqa: F401
import ctranslate2  # noqa: F401
from faster_whisper.audio import decode_audio

wav = "desktop/checks/voice.wav"  # run from the repo root
samples = len(decode_audio(wav))
assert samples == 1600, samples
print(f"parts load: {ssl.OPENSSL_VERSION}, av {av.__version__}; voice decodes")
