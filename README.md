# ken ●

**Your assistant, on your computer.**

Ken is an AI assistant that lives on your Mac. Text or talk to it from your desk or your
phone, and it does the work: it reads your email and calendar, browses the web in your
Chrome, makes files, runs things on a schedule, and checks with you before anything goes
out. It runs on the Claude or ChatGPT subscription you already have, and everything it
knows stays on your computer.

🌐 **[kencomputer.dev](https://kencomputer.dev)**

## Get it

**Mac:** download [Ken for Apple Silicon](https://github.com/prasann16/kencomputer/releases/latest/download/Ken-arm64.dmg)
or [Ken for Intel Macs](https://github.com/prasann16/kencomputer/releases/latest/download/Ken-x64.dmg),
drag it into Applications and open it. Then:

1. **Connect Claude or ChatGPT.** One click signs in with the subscription you already have
   (Claude Pro or Max, or a paid ChatGPT plan through Codex). No API keys, no per-token bills.
2. **Add your phone (optional).** Scan a QR code and Ken is in your Telegram: same Ken, same
   conversation, text or voice notes from anywhere.

Ken updates itself.

**Linux or a server:** one command installs the same Ken, run from the terminal and reached
through Telegram. A $5 VPS is a fine home; it never sleeps there.

```bash
curl -fsSL https://kencomputer.dev/install | bash
```

## What it does

- **One conversation.** Ask for anything in plain words, in the app or on Telegram. Ken does
  the work itself and tells you what it did.
- **Voice notes**, transcribed on your computer (the audio never leaves it).
- **A morning brief** with what matters today, and a **nightly review** that files the day
  into Ken's memory. Pick the times, or add your own scheduled jobs in Settings → Jobs or by
  just asking.
- **Memory.** Tell Ken something once and it remembers. Everything it knows is in
  **Brain**: who Ken is, what it remembers, and the accounts and passwords you've given it,
  all readable and editable.
- **Your tools.** Chrome (your signed-in browser), email and calendar connect in
  Settings → Tools. Anything with a CLI, an API or an MCP server, Ken can set up itself;
  [recipes](recipes/) cover the fiddly sign-ins.
- **Claude and ChatGPT, side by side.** Connect either or both, and pick the AI and model
  for each chat from the menu at the top.
- **Agents.** Ask Ken to give an ongoing job its own agent ("run my newsletter"), with its
  own chat, memory, schedule and tools. Ken, the chief of staff, sees all of them.
- **Asks first.** Ken checks with you before sending email, posting publicly, merging or
  spending money.
- **Light and dark**, and an optional "keep this Mac awake" so Ken works while you're away.
- **Another Mac (optional).** Settings → Computers lets you use Ken running on another Mac,
  over a Cloudflare tunnel you control.

## Requirements

- A Mac (Apple Silicon or Intel) for the app, or macOS/Linux with `git` for the terminal install
- A Claude subscription (Pro or Max) or a paid ChatGPT plan
- Telegram, only if you want Ken on your phone

## Privacy and security, honestly

- **It's a real assistant with real access.** Ken runs as your user and can do what you can
  do in a terminal, without asking for each command. That's what makes it able to do things.
  Run it on a computer you're comfortable giving it.
- **Your data stays on your computer.** Conversations, memory, files and passwords live in
  `~/.ken/`, as plain files you can read and delete. Not encrypted at rest, so treat that
  computer like the personal computer it is. Passwords are only readable by you.
- **What leaves it:** your messages go to Claude or ChatGPT to be answered, the same as using
  them directly, and through Telegram if you use Ken on your phone. Anonymous crash reports
  and usage counts (never what you say, your files or your logins) can be turned off in
  Settings → Privacy. [Exactly what's sent](https://kencomputer.dev/privacy).
- **Nothing is reachable from outside.** The app talks to Ken on `127.0.0.1` with a private
  token. Using Ken from another Mac is off unless you turn it on.
- **Only you.** On Telegram, Ken answers only your account.

## Terminal install: manage it

```
ken open       open Ken in your browser
ken status     is it running?
ken logs       recent activity
ken update     pull the latest version and restart
ken memory     edit what Ken knows about you
ken setup      connect Telegram and Claude again
ken config     edit settings
ken uninstall  remove Ken (keeps your memory & settings)
```

## Development

Ken is one engine (`bot.py`, `engine.py`, `brains.py`) with windows onto it: the chat page in
`web/`, shown in the Mac app (`desktop/`), and Telegram. See the
[desktop guide](desktop/README.md) for running a checkout (`./dev.sh`), building, releasing
and tests (`tests/check.sh`). Setting up servers for someone else? See the
[VPS deployment guide](deploy/README.md).

## License

MIT, no warranty. Built by [Prasann](https://x.com/prasann_pandya), who talks to his Ken every day.
