# ElevenLabs client, served by Ken itself

Voice mode (hosted Ken) talks to an ElevenLabs agent. Ken's page only runs its own scripts (CSP `script-src 'self'`),
so the client and its audio worklets are copied here instead of loaded from a CDN:

- `client.js`: `@elevenlabs/client` 1.30.0, `dist/lib.iife.js` minified with esbuild (exposes `window.ElevenLabsClient`).
- `rawAudioProcessor.js`, `audioConcatProcessor.js`: the same package's `worklets/` (its README: "Self-hosting
  AudioWorklets under a strict CSP").
- `libsamplerate.worklet.js`: `@alexanderolsen/libsamplerate-js` 2.1.2, `dist/libsamplerate.worklet.js`. Only loaded by
  browsers that can't record at another sample rate (Firefox).

Both packages are MIT licensed. To update: `npm pack` the new versions and repeat the steps above.
