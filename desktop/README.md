# Ken for Mac

A thin window onto the local `ken` service. There is one backend, `bot.py`, which the installer runs as a background service. It owns the engine, memory, schedules, voice transcription and (optionally) Telegram, and it serves the chat UI from `web/` at `127.0.0.1:7777`.

The app itself only:

- starts the service with `ken start` if it isn't already running
- signs in with the token in `~/.ken/web-token` (set as a cookie, never in a URL)
- shows the chat, asks macOS for microphone access, and keeps a menu-bar icon

Closing the window hides it. Quitting the app leaves the service (and Telegram) running.

Telegram and the Mac app share everything: the same conversation, the same `voice.py` transcription, and the same commands. `/model`, `/coffee` and `/decaf` are plain functions in `bot.py` exposed to the chat through `POST /api/command`. `/new` and `/stop` use the chat endpoints.

## Development

```sh
ken stop                              # free the port if the installed service is running
.venv/bin/python bot.py               # this repo's service; leave TELEGRAM_BOT_TOKEN unset for app-only
npm ci && npm run desktop
```

## Build

```sh
npm run pack        # dist/desktop/mac/Ken.app
npm run dist:mac    # .dmg
```

The app contains no Python, so a UI or engine change ships with `ken update`, not a new `.app`. Signing and notarization are not configured.

## Tests

```sh
.venv/bin/python -m pytest tests -q --asyncio-mode=auto
npm run test:desktop
```
