# Ken desktop

Ken's first desktop build uses the existing Python conversation engine with an Electron shell. It runs locally without Telegram. The UI uses the approved white, single-chat design with native system typography, a small Ken character in the header, and real message, task, and file data.

## Code layout

The desktop adapter lives in `desktop/`: Electron window/browser code, `runtime.py` (startup), and `services.py` (desktop integrations). The development launcher runs `python -m desktop.runtime`. Both desktop and Telegram import the same root `engine.py`, `brains.py`, and `voice.py`; `web.py` and `web/` supply the shared local chat interface. Telegram's entry point remains `bot.py`. Do not fork the engine or transcription code for an interface.

## Included

- A compact 900 × 700 chat window with the native system font, dark text, attachments, and local voice transcription.
- Immediate message display on Enter, delivery status, and retry without duplicate work. Chat refreshes independently of file scans.
- A compact title row places Ken beside the native Mac window controls. The header has Clear for a fresh thread and a Claude account status/reconnect menu. /new, /stop, and /model are discoverable by typing /; memory and routines are configured in chat. Chrome setup is deferred.
- /new (or “clear this conversation”) starts a fresh Claude session and archives the visible conversation by an event boundary. It keeps the underlying transcript, memory, files, and schedules. Stop or finish an active reply before clearing.
- Chrome setup is deferred; the underlying bridge remains available for future interface work.
- File previews for images, video, audio, and text. Pending decisions and ongoing work stay inline with the conversation.
- Tray/menu-bar access and a bundled Python engine. Closing the window keeps Ken running; Quit stops it.

Claude is the only implemented provider. The app detects an existing Claude login or starts the bundled official CLI subscription sign-in flow in the browser. It does not yet include other model providers, turnkey service connectors, a dedicated marketing/video editor, remote hosting, automatic updates, or launch at login. The agent can work with tools and services already configured on the machine. Native Windows execution and packaging still need validation on Windows.

New setups activate each routine only after the user chooses it; declining or postponing it records a paused choice. Previously configured schedules stay intact. The daily memory check reuses the existing nightly-review job, reviews local conversation history, updates useful notes, and posts a short summary in the chat. The scheduler runs while the app is open, including with its window hidden. Missed jobs catch up once on the same day when the app runs again. Computer sleep pauses local work. Schedules use the computer's local time.

## Development

Use Python 3.11+ and Node 22+ on the target operating system.

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-desktop.txt
npm ci
npm run desktop
```

On Windows, replace `.venv/bin/python` with `.venv/Scripts/python.exe`. Set `KEN_PYTHON` to override interpreter discovery. `KEN_HOME` selects a separate data directory for development; by default Ken uses `~/.ken`, preserving the existing memory, projects, and conversation database. Stop the older background service with `ken stop` before using the desktop app against the same home.

Voice uses the same voice.py implementation as Telegram: faster-whisper, WHISPER_MODEL from ~/.ken/.env (default small), CPU/int8, and no VAD. Both runtimes preload it at startup and reuse the local model cache without a network check. A download is needed only on a machine without that model.

## Build

Build on each target OS and architecture. The Python engine must match that target.

```sh
uv pip install --python .venv/bin/python pyinstaller
npm run build:engine
npm run pack
```

The unpacked macOS application is `dist/desktop/mac/Ken.app` for an Intel build. Use `npm run dist:mac` or `npm run dist:win` for installers after building the engine on that platform. Development builds deliberately disable signing-identity discovery; release signing, macOS notarization, and Windows signing must be configured before public distribution. No publishing occurs in these scripts.

## Validation

```sh
uv pip install --python .venv/bin/python pytest pytest-asyncio pytest-aiohttp python-telegram-bot
.venv/bin/python -m pytest tests -q --asyncio-mode=auto
npm run test:desktop
npm run test:ui
```

UI tests use a disposable home, a fake model, and a local browser fixture. They cover account gating, immediate message display, draft preservation, slash commands, clear-thread persistence, a waveform from an actual audio stream, voice message and transcript persistence, attachments, file previews, and compact layout. Screenshots are written to the ignored `test-results/` directory. They never use the real Ken database or make billable model requests.

## Boundaries

The desktop engine uses the SDK's `bypassPermissions` mode, equivalent to Claude Code's `--dangerously-skip-permissions`. No `can_use_tool` callback is attached, so tool execution does not wait for approval clicks. This applies to chat, background jobs, and browser tools. Project autonomy lists and explicit user instructions still guide the assistant's behavior; they are not an OS sandbox. Operating-system permissions, website authentication, and browser isolation remain in effect. The Telegram runtime uses the same SDK permission mode.

The local API binds to loopback on a dynamically allocated port and authenticates with a token stored under the Ken home. Electron installs its HttpOnly cookie without placing the token in a URL. App renderers are sandboxed, have no Node integration, and use context isolation. Website content runs in Chrome, outside Electron, without app preload APIs. Ken connects using Playwright CDP with no default context overrides; it does not copy cookies or launch a new Chrome profile. Only the app's main frame can call the small native bridge. Active uploaded documents download rather than executing under the app's origin; text previews are rendered as text.

Files and conversations remain on disk, but prompts and relevant content are sent to Claude when it works. Explicit sign-out and encrypted storage are not implemented by this prototype. Account reconnect uses the bundled Claude CLI; Ken does not collect passwords.

## Browser bridge (deferred UI)

The existing Chrome bridge remains in the desktop adapter, with its origin checks and browser isolation. Connection controls are intentionally absent from the minimal chat UI. It is not part of the first-run flow. There is no claim of automatic access to a signed-in Chrome profile.

## Conversational setup

The opening message invites a voice note about the user's work and what takes too much time, with a typed fallback. Ken should help complete one useful task before offering more automation. Recording and transcription state are visible beside the composer. The mic replaces the composer with a live microphone waveform, elapsed time, Cancel, and Send. Send immediately places a playable voice note in chat, frees the composer, and shows the transcript when ready. The original audio is saved in the chat files; waveform and duration are kept with the accepted message. Failures offer Retry on the voice note and retain the audio. Typed drafts remain untouched. Desktop native transcription runs in a preloaded child process using the shared transcriber; a stalled worker is terminated and restarted for the next request.

Names, preferences, morning briefs, and memory reviews are configured through chat using `ken.preferences`. Existing jobs and memory remain intact. Project records remain in the engine for existing data and chat tools, without a separate project UI. The existing Claude Agent SDK authentication and bypassPermissions configuration are unchanged. No payment flow has been added. Claude sign-in uses the official bundled CLI.


### Recording regression checks

Desktop requests macOS microphone access only when the user starts a recording and reuses an existing grant. It uses the browser’s normal speech-capture defaults; microphone leveling, noise suppression, and echo cancellation are no longer forcibly disabled. The waveform uses floating-point samples, so quiet input is still visible to the signal check. Send stays disabled until actual input is measured; a silent input offers a link to the OS sound-input settings.

The transcription adapter rejects exact digital silence before invoking Whisper. This is not a VAD threshold and does not discard quiet speech. A saved voice note retains its audio for playback, while only its transcript goes to the shared conversation engine. The renderer policy allows same-origin and temporary blob media playback; active documents remain restricted. Regression tests cover both microphone consent reuse and this transcript-only model input.
