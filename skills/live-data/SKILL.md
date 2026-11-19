---
name: live-data
description: Live public data from the web on hosted Ken, paid from their Ken AI credits - viral and trending reels on Instagram, TikTok and YouTube, any public account's posts, reels and stats (competitor research), hashtag and keyword searches, transcripts of someone's reel, trends, SEO and keyword data, web search and page scraping, company and people lookups. Use when they ask what's going viral in their niche, for reels or posts like X, how a creator's videos are doing, trending sounds or topics, hooks that work for others, keyword or search volume, or any fact that needs fresh data from a site Ken can't simply open. Not for posting or acting on their own accounts (that's their connected apps).
---

# Live data

Thousands of data tools from treg's catalog, run on Ken's own account through the host. Each call is paid from their
Ken AI credits: treg's price plus Ken's margin (10%). Most calls cost a fraction of a cent.

The helper is `<ken> --tools <command>`, where `<ken>` is the exact command in your instructions (the same one the
video kit uses). Every command prints JSON.

1. `<ken> --tools search "instagram reels by hashtag"`: say the job, not a provider. It lists tools with their price
   (`cost`). Prefer a `treg.*` tool (routed: it picks the best provider and moves on when one fails).
2. `<ken> --tools tool <id>`: its parameters, price and an example call. Read it before the first call to a tool.
3. `<ken> --tools call <id> --max <dollars> --data '{"username": "natgeo"}' --label "competitor reels"`:
   runs it. `--max` is the most this one call may cost at treg's price (a little above the listed price; a
   per-row price times the rows you ask for). It's refused, free, if it would cost more. A GET tool takes
   `--query key=value` instead of `--data`. The first line says what it cost (`charged_usd`, from their credits)
   and what's left; then the answer, or `saved` with a file path when it's big.

## Money

- Tell them what a task will cost before you start a run of more than a few calls, and the total after. Keep a
  running sum of `charged_usd` and stop if it heads past what they agreed to.
- Ask first before a task that will cost more than about $0.50 of their credits.
- A call that fails or is refused costs nothing. Don't retry a failed call in a loop: read the error, fix the
  request once, or try another tool for the same job.
- `code: credits`: their Ken AI credits can't cover it. Say so plainly with the link in the error.
- `code: off` or `not_live`: the live data tools aren't switched on for them. Say it isn't available yet; don't
  look for a way around it.

## Doing it well

- Ask for only what you need (`limit`, a few accounts, recent posts). Rows cost money.
- Viral in their niche: hashtag or keyword reel search, then sort by views/plays relative to the account's
  followers; show the top few with links, numbers, and why each worked (hook, format, length). Offer to write a
  version in their own style.
- Competitors: their public account's recent reels with views and dates; the outliers are what to learn from.
- Never present scraped numbers as exact if the tool marks them estimated.
- This is only for public data. To post, upload or read their own inbox, use their connected apps instead.
