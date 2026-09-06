# Ken for the menu bar

A dot at the top of the screen that says whether Ken is alive, with a menu for
the things people otherwise need a terminal for: restart, update, logs, memory,
settings, start at login. Nothing else changes — it runs the exact same
`bot.py` the installer sets up.

```
●  Ken is running
──────────────────
Restart Ken
Stop Ken
Update Ken
──────────────────
View Logs
Edit Memory (SOUL.md)
Edit Settings (.env)
──────────────────
✓ Start at Login
Quit (stops Ken)
```

If Ken isn't installed yet, the menu offers **Install Ken…**, which opens
Terminal running the one-line installer. When it finishes, the app notices and
takes over.

## How it takes over

The installer registers a launchd agent to keep Ken running. When this app
starts it boots that agent out and disables it, then runs `bot.py` itself as a
child process and restarts it whenever it exits (Ken's hourly self-update works
by exiting, so this matters). It writes `~/.ken/app.pid` so the `ken` CLI knows:
`ken restart` sends the app a signal instead of touching launchd, and
`ken start`/`ken stop` tell you to use the menu.

Quitting the app stops Ken. To go back to the plain background service, run
`ken start` — that re-enables the launchd agent.

Running `bot.py` as a child of a real app is also what gets you the macOS
privacy prompt for Desktop, Documents and Downloads with "Ken" as the name,
instead of a background `python3` being silently denied.

## Build

```
cd macapp && ./build.sh
open dist/Ken.app
```

Needs the Xcode Command Line Tools, nothing else. Produces a universal binary
for macOS 13+. The default build is ad-hoc signed: it runs on your Mac, and on
someone else's after right-click → Open the first time.

## Ship

To distribute without the right-click dance you need an Apple Developer
account ($99/yr), a "Developer ID Application" certificate, and notarization:

```
CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./build.sh
ditto -c -k --keepParent dist/Ken.app dist/Ken.zip
xcrun notarytool submit dist/Ken.zip --keychain-profile ken --wait
xcrun stapler staple dist/Ken.app
```

(`notarytool store-credentials ken` once, with an app-specific password.)
Then zip the stapled app and put it behind a download link.

## Testing without touching a real install

`KEN_HOME` is honoured, so you can point the app at a fake install:

```
KEN_HOME=/tmp/fakeken dist/Ken.app/Contents/MacOS/Ken
```

with `venv/bin/python`, `app/bot.py` and `.env` present under that folder.
