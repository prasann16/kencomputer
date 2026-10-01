# ken ●

**Give your AI its own computer.**

Ken is Claude Code with a body: always on, reachable from your phone via Telegram — text or voice notes — with persistent memory, on a machine you own. Ship a fix from a hike. Have it plan your trip mid-run. Tell it something once; it knows it forever.

> You already pay for Claude. Ken gives it a body — heartbeat, hands, a home, and a memory.

🌐 **[kencomputer.dev](https://kencomputer.dev)** · hosted version waitlist there too

## Install

One command, on a Mac or any Linux box (a $5 VPS is Ken's favorite home — it never sleeps there):

```bash
curl -fsSL https://kencomputer.dev/install | bash
```

~2 minutes. The installer walks you through everything:

1. **Create a Telegram bot** — message [@BotFather](https://t.me/BotFather), send `/newbot`, paste the token.
2. **Pair your account** — send the installer's one-time code to your new bot in a private chat; Ken locks itself to your Telegram account.
3. **Connect Claude** — sign in with the Claude subscription you already have. No API keys, no per-token bills.

Then just talk to it.

## What you get

- **A real assistant on a real computer** — it runs Claude Code (Anthropic's own agent) with full access to its machine: installs tools, runs code, browses the web, manages projects, sets up its own cron jobs.
- **Voice notes** — transcribed locally on your machine (never sent to a third party), then handled like text.
- **A morning brief** — every day, first thing, without asking: open loops, what's on today, weather if it knows your city. Pick the time in your first conversation; a nightly review writes memory so tomorrow's brief knows more.
- **Persistent memory** — Ken keeps notes on you, your projects, and your rules in `~/.ken/work/SOUL.md` — its soul file. Tell it once — it knows tomorrow. It updates its own memory when you say "remember…".
- **Model switching** — `/model opus`, `/model haiku`, `/model default`. Live list from your own account.
- **Controls** — `/stop` kills the running task, `/new` starts a fresh conversation.

## Connecting things

Ken connects to services by following **recipes** — markdown playbooks it reads and acts on
([recipes/](recipes/)). Say "connect my email" and it walks you through it in the chat:
direct links for the bits only you can do, credentials stored on your machine at
`~/.ken/credentials/` (chmod 600), a verification step so you know it worked.

No plugin system, no marketplace: if a service has a CLI, an API, or an MCP server, Ken can
use it — recipes just mean it doesn't have to figure out the fiddly sign-in dance twice.
Wrote one? PRs welcome.

## Desktop app

Ken now has a desktop prototype with Today, conversations, projects, a connection to your signed-in Chrome, file previews, local voice input, and scheduled briefs. It uses the existing engine and local memory; Telegram is optional. See [desktop setup, builds, and current limitations](desktop/README.md).

```sh
npm ci
npm run desktop
```

Install the Python desktop dependencies first as described in the desktop guide. Mac has been exercised locally; Windows build configuration is included and needs native validation.

## The app

`ken open` opens Ken as an app: a chat with Ken, plus one chat per project. Same Ken, same
memory, the same conversation you have on Telegram. A project is a folder in
`~/.ken/projects/<id>/` with its context (`CONTEXT.md`) and whatever files you drop into
its chat; Ken reads the context with every message and keeps it current. Drop files in and
Ken looks at them straight away. It updates when Ken does, and on a phone you can add it
to your home screen. Telegram is optional.

## Manage it

```
ken status     is it running?
ken logs       recent activity
ken update     pull the latest version and restart
ken memory     edit what Ken knows about you
ken config     edit settings
ken uninstall  remove the service (keeps your data)
```

## Requirements

- macOS or Linux, `python3` (3.9+), `git`
- A [Claude](https://claude.ai) subscription (Pro or Max)
- A Telegram account

## Security notes, honestly

- Ken runs Claude Code with `--dangerously-skip-permissions` in its workspace — that's what makes it *able to do things*. Run it on a machine you're comfortable giving it. A cheap dedicated VPS is the sweet spot: full power, blast radius of one.
- Only your Telegram user ID gets answered; everyone else is ignored.
- Your credentials live in `~/.ken/.env` (chmod 600) on your machine — nowhere else.
- Everything Ken remembers is plaintext on your disk: `SOUL.md`, its journal, and full conversation transcripts in `~/.ken/history/`. Readable and deletable by you at any time — and not encrypted at rest, so treat that machine like the personal computer it is.
- What it can and can't reach: it runs as your user, so it can do what you can do in a terminal — but it has no admin rights unless you grant them, and on macOS the system keeps background processes out of Desktop, Documents, Downloads, Photos, Calendar and Contacts until you explicitly allow it in Privacy & Security.
- Nothing listens on any port. The bot polls Telegram outbound; there is no inbound surface.
- The entire harness is [one Python file](bot.py) — read it over coffee.

## The hosted version

Don't want to run anything? [kencomputer.dev](https://kencomputer.dev) — we stamp a hardened server with Ken preinstalled, you connect two accounts, done. Join the waitlist.

Setting up fresh boxes for the pilot? See the [VPS deployment guide](deploy/README.md)
for preparation, owner onboarding, service checks, and backup/restore drills.

## License

MIT — do whatever, no warranty. Built by [Prasann](https://x.com/prasann_pandya), who talks to his Ken every day.
