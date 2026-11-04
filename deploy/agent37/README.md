# Ken on Agent37

Ken on its own computer in the cloud, used in a web browser: one [Agent37](https://www.agent37.com) instance per
person, made from this folder's image. It's the same Ken (memory, scheduled jobs, chat, skills); only the
computer differs.

## What's in the image

- `Dockerfile`: `FROM ghcr.io/agent37-platform/base` (Node, Python, git, uv; user `node`). It bakes in what doesn't
  change per person: Python 3.13 and Ken's packages in `/opt/ken/venv`, a first copy of Ken's code in
  `/opt/ken/app-seed` (cloned from `KEN_REF`, `main` by default), ffmpeg, Chromium and Google's Chrome DevTools MCP.
  Voice is left out (`KEN_VOICE=0`, `pins.py`): the local transcription model needs about 1.5 GB of memory.
- `ken-start`: the instance's main process. It starts `ken-supervise` and then the base image's own boot, which runs
  the `~/.agent37/hooks` scripts and serves a terminal on port 7681.
- `ken-supervise`: there's no systemd here, so this keeps Ken running. On the first boot it puts Ken's code, memory
  folders, standing jobs and `hosting.md` on the persistent disk (`~/.ken`); on every start it updates Ken
  (`git pull`, then its Python packages); and it starts Ken again whenever it exits, which is how Ken's own hourly
  self-update restarts into a new version (a little slower each time if it keeps failing fast).

`/home/node` is the instance's persistent disk and hides anything the image puts there, so Ken's code, `.env`,
memory and history live in `~/.ken` on the disk, and an image update (which resets everything else) keeps them.
`~/.ken/venv` points at `/opt/ken/venv`; after an image update the next start reinstalls whatever the code on the
disk needs into it.

## Build and register

```sh
cd deploy/agent37
AGENT37_API_KEY=sk_live_... npx agent37 templates build . --name ken --default-port 7777
```

The build runs on Agent37 (no local Docker) and publishes a new revision of the `ken` template. New instances use
it; to move an existing instance onto it, `POST /v1/instances/{id}/update` (its disk is kept). The CLI drops `.git`
from the build folder, which is why the image clones Ken instead of copying it. To build from a branch, change
`ARG KEN_REF` in a copy of the Dockerfile.

Create instances with `"template": "ken"`, `"auto_sleep": true` and an `idle_timeout_seconds` (300 works well).
Ken's web app is the instance's main URL (port 7777, on `0.0.0.0`).

## Settings (instance environment)

All optional. Set them when creating the instance; a change reaches Ken on the instance's next restart.

| Variable | What it does |
| - | - |
| `KEN_WEB_TOKEN` | Ken's app token (24+ characters). A service that signs people in itself forwards each request with `Authorization: Bearer <token>`; without it Ken makes its own (`~/.ken/web-token`). |
| `KEN_KEEPALIVE_URL`, `KEN_KEEPALIVE_TOKEN` | Every minute while awake Ken POSTs `{"busy", "next_job", "rev"}` here with the token as a Bearer. Work inside the computer doesn't count as activity to Agent37, so the service keeps a busy computer awake (any request to its URL does) and wakes it when `next_job` (epoch seconds) is due. |
| `KEN_ACCOUNT_URL` | A link in Settings for the person's plan and billing (e.g. `/account`). |
| `KEN_BROWSER` | `1` gives Ken its own headless Chromium (browser tools). Off otherwise. |
| `KEN_INCLUDED_AI_URL`, `KEN_INCLUDED_AI_TOKEN` | AI included with a plan, until the person connects their own Claude: Claude Code talks to this Anthropic-compatible endpoint (https) with the token as a Bearer. The host's endpoint holds the real provider key and meters each person's spend, so no provider key is ever on the computer (Ken can run commands, and could be asked to print its environment). When the endpoint refuses with 402 (the month's Ken AI credits are used up), Ken says so in plain words ("You've used this month's Ken AI credits.") with a button to connect their own Claude or ChatGPT. |
| `KEN_INCLUDED_AI_MODEL` | The model the included AI runs on (Ken's model picker shows what the endpoint's `/v1/models` lists). |
| `TELEGRAM_API_URL`, `TELEGRAM_BOT_TOKEN`, `KEN_TELEGRAM_PUSH=1` | Ken in Telegram through the host. Ken's Telegram code talks to the host's stand-in for Telegram's Bot API (`<url>/bot<token>/<method>`, files under `<url>/file/bot<token>/`) with a token of the host's choosing, so the real bot token stays with the host. A sleeping computer can't fetch updates, so the host pushes them: `POST /api/telegram/update` with Ken's web token, one Telegram update as JSON (`503` while Ken's Telegram is still starting: try again). Whoever the pushed updates are from is the owner; `{"ken_relay": "unlinked"}` means they disconnected. |
| `KEN_OWNER_NAME` | The person's first name, for Ken's first soul file. |
| `KEN_APPS` | `0` turns off the apps tools. Otherwise Ken gets Agent37's managed Composio MCP server (Gmail, Calendar and more) as `apps`; its calls are metered against the instance's budget, so the budget's monthly cap is the app-actions cap. With a host (`KEN_KEEPALIVE_URL`), Ken runs `ken --apps [app]`, which asks the host's `/box/apps` with the check-in token what's connected and for a one-tap Connect link; the host then calls `POST /api/apps/connected` and the chat that gave the link carries on. Settings → Apps lists connected apps and links to the host's Apps page. |
| `KEN_PUBLIC_ORIGINS` | Extra page addresses Ken trusts. Set by `ken-supervise` to the instance's own URLs. |

Set by the image (leave them): `KEN_PLATFORM=agent37` (browser mode: no Mac features, no phone step, sign-in that
works from afar), `KEN_HOSTED=1`, `KEN_VOICE=0`, `KEN_WEB_HOST=0.0.0.0`, `KEN_WEB_PORT=7777`, `KEN_CHROMIUM`,
`KEN_NODE`, `KEN_BROWSER_MCP`.

## In front of Ken

Agent37's edge hands Ken an internal address as its host and replaces response security headers. A service that
forwards to Ken should:

- send `X-Forwarded-Host: <its own host>` (Ken trusts its page there) and `X-Agent37-Key`, and never let either
  reach the browser;
- forward the WebSocket at `/api/events`;
- set the page's `Content-Security-Policy` itself (Ken's `index.html` also carries one in a `<meta>` tag), and
  `sandbox; default-src 'none'` on `/api/file` and `/api/chats/*/files/*`;
- optionally pass what's left of the person's plan in `X-Ken-Usage`, JSON like
  `{"plan": "…", "lines": [{"label": "Awake time", "left": "3 h", "of": "10 h", "low": false}], "note": "…", "manage_url": "/account"}`.
  Ken shows it in Settings and a line above the chat when something is low.

## Connecting an AI on a computer in the cloud

There's no browser on the instance, so Settings → AI accounts (and the first screen) offers ways that work from
anywhere:

- Claude: sign in and paste the code Claude shows; paste a token made with `claude setup-token` on any computer;
  or an Anthropic API key.
- ChatGPT: a device code (open a page, type the code shown); or an OpenAI API key.

## Checks

```sh
.venv/bin/python -m pytest tests/test_hosted.py -q
```

On a real instance: Ken's own log is the instance log (`GET /v1/instances/{id}/logs`); `ken-supervise` prefixes its
lines with `[ken]`.
