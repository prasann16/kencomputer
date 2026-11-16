# Ken's own Telegram Bot API server

Telegram's servers let bots download files up to 20 MB and upload up to 50 MB. A creator's full-quality video is often
hundreds of MB, so Telegram would say no. Telegram publishes its Bot API server; run it ourselves in `--local` mode
and bots download files up to 2 GB. ken-hosted talks to it instead of api.telegram.org, and every hosted Ken gets the
bigger limit with no change on the person's side.

What's here:
- `bot-api`: Telegram's server (aiogram/telegram-bot-api image). Needs an API ID and hash from my.telegram.org.
- `door`: nginx in front of it. It lets in only requests that carry the relay secret (`x-ken-relay`). Files are
  served from the bot's own folder only, and nothing else can be reached.
- `tunnel` (profile `public`): a Cloudflare tunnel, so the door is reachable from the website with no open port.

Tested on 2026-11-08 with a test bot: a 96 MB video went out (Telegram's limit is 50 MB) and came back through the
door byte-identical (Telegram's limit is 20 MB). Without the secret, requests get a 403. Another bot's folder, or `..`,
gets a 404.

## Switching hosted Ken over (when approved)

1. On the server: put `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `KEN_RELAY_SECRET` (random) and
   `CLOUDFLARE_TUNNEL_TOKEN` in `.env`, then run `docker compose --profile public up -d`. Point the tunnel at
   `http://door:8080` under a hostname such as `tg.kencomputer.dev`.
2. For each bot (the shared bot, and each person's own bot in `telegram_bots`), call `logOut` once on
   api.telegram.org. Then call `setWebhook` on the new server with the same URL and secret_token as before.
3. Website secrets and vars (ken-hosted, `--env production`): `TELEGRAM_API_URL=https://tg.kencomputer.dev`,
   `TELEGRAM_RELAY_SECRET=<same secret>`, `TELEGRAM_BIG_FILES=1`. Deploy. Each Ken picks up the 2 GB download limit
   on its next boot.

To go back: call `logOut` on our server, wait about 10 minutes (Telegram's rule), unset the three settings, and set
the webhooks on api.telegram.org again.

Files Ken sends still go through the website, which takes up to 100 MB per request, so Ken keeps sending up to 50 MB
and links anything bigger. The creator storage work (R2) streams big files from R2 to this server instead.
