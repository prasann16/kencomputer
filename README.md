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
2. **Say hi** — message your new bot once; Ken locks itself to your Telegram account.
3. **Connect Claude** — sign in with the Claude subscription you already have. No API keys, no per-token bills.

Then just talk to it.

## What you get

- **A real assistant on a real computer** — it runs Claude Code (Anthropic's own agent) with full access to its machine: installs tools, runs code, browses the web, manages projects, sets up its own cron jobs.
- **Voice notes** — transcribed locally on your machine (never sent to a third party), then handled like text.
- **Persistent memory** — Ken keeps notes on you, your projects, and your rules in `~/.ken/work/CLAUDE.md`. Tell it once — it knows tomorrow. It updates its own memory when you say "remember…".
- **Model switching** — `/model opus`, `/model haiku`, `/model default`. Live list from your own account.
- **Controls** — `/stop` kills the running task, `/new` starts a fresh conversation.

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
- Nothing listens on any port. The bot polls Telegram outbound; there is no inbound surface.
- The entire harness is [one Python file](bot.py) — read it over coffee.

## The hosted version

Don't want to run anything? [kencomputer.dev](https://kencomputer.dev) — we stamp a hardened server with Ken preinstalled, you connect two accounts, done. Join the waitlist.

## License

MIT — do whatever, no warranty. Built by [Prasann](https://x.com/prasann_pandya), who talks to his Ken every day.
