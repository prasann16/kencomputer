# Recipe: connect video generation (HeyGen avatar API)

Gives you the ability to turn a script into an actual talking-avatar video — useful for
a recurring job (e.g. a daily build-in-public draft) that should hand back a finished
clip, not just a caption someone still has to go record.

## Why HeyGen, not Sora/Runway/Veo/Luma/Kling

All five do text-to-video well, but none of them keep the same face and voice across
videos — they generate a new scene each time. For "here's an update from me," that's the
wrong shape: you want the same presenter every day, not a new stranger. HeyGen's avatar
API is built for exactly that (script in, same avatar/voice speaking it, out), the
request is one plain REST call with an API key — no SDK, no polling infrastructure
beyond a GET in a loop — and it's the cheapest of the bunch for a short daily clip
(roughly $0.02–$0.07 per second of output, so a 20-second clip is well under a dollar).

If a scene/b-roll shot is what's actually wanted instead of a person talking — a
generated clip to cut into something else — reconsider and use Runway, Veo, or Kling
instead; this recipe doesn't cover those.

## The rule that matters most

**Generate freely, post only on an explicit yes.** Calling the API and producing a video
can run unattended — it costs money but nothing public happens. Posting it anywhere
(Instagram, X, wherever) is the same rule as email: never as a side effect of a
scheduled job, only when they say so in that moment, after they've actually seen the
clip. A posted video can't be unposted either.

## Step 1 — ask what avatar to use

HeyGen ships a large library of stock avatars and voices — good enough to start with
today. Building a personal "digital twin" avatar (trained on a short video of them) is
a separate, one-time step on heygen.com itself; mention it as a later option, don't
block on it. Ask: stock avatar for now, or do they want to set up their own first?

## Step 2 — get an API key

Give them the direct link: **https://app.heygen.com/settings?nav=API** (Settings → API →
API token on the HeyGen dashboard). It's shown once — they must copy it immediately,
regenerating invalidates the old one. Since February 2026 the API has no free tier: it's
pay-as-you-go, topped up in advance (as little as $5). Say that plainly before they go
set up billing.

## Step 3 — store it

`~/.ken/credentials/heygen` — one JSON file: `{"api_key": "...", "avatar_id": "...",
"voice_id": "..."}`. `chmod 700` the folder, `chmod 600` the file, same as every other
credential. Leave `avatar_id`/`voice_id` out until Step 4 fills them in.

## Step 4 — verify, and pick defaults

```
GET https://api.heygen.com/v2/avatars   (header: X-Api-Key: <key>)
GET https://api.heygen.com/v2/voices
```

`httpx` is already a Ken dependency — no install needed. List a handful of each, let
them pick (or just pick a sensible default yourself — "Daisy" or similar professional
stock avatar, one clear English voice), and save `avatar_id` + `voice_id` into the
credential file from Step 3. Report back concretely: which avatar, which voice, and that
the key works.

## Step 5 — record it

Add to `SOUL.md` under "What I can do here": video generation is connected, which avatar
and voice are the default, and the generate-freely-post-on-yes rule. If this is meant to
feed a recurring job (e.g. a daily content draft), that job's prompt is what should call
this — note the connection there, not here.

## Using it afterwards

Generating a video:

```
POST https://api.heygen.com/v2/video/generate   (header: X-Api-Key: <key>)
{
  "video_inputs": [{
    "character": {"type": "avatar", "avatar_id": "<avatar_id>"},
    "voice": {"type": "text", "input_text": "<script, under 1500 characters>", "voice_id": "<voice_id>"}
  }],
  "dimension": {"width": 1080, "height": 1920}
}
```

`1080x1920` is vertical — right for Instagram/TikTok/Shorts; use `1920x1080` for
landscape. The response gives a `video_id`; poll until it's done:

```
GET https://api.heygen.com/v1/video_status.get?video_id=<video_id>   (header: X-Api-Key: <key>)
```

`status` moves through `waiting` → `pending`/`processing` → `completed` (or `failed`,
with an `error` field explaining why). A short clip usually finishes in well under a
minute; check every 5–10 seconds rather than hammering it. On `completed`, `data.video_url`
is a real download link — fetch it promptly, since `video_url` (and `thumbnail_url`)
expire after 7 days even though the video itself doesn't. Save the file into
`~/.ken/outbox` so it reaches them, same as any other file you hand back (see the main
README's SENDING FILES section) — then show it to them and wait for their yes before
anything gets posted anywhere.

## When it goes wrong

- `401`/invalid key → they pasted it wrong, or it was regenerated since. Ask them to
  check Settings → API again.
- `400` with a credits/balance error → the prepaid wallet is empty; point them back to
  the API billing page, don't retry in a loop.
- Script over ~1500 characters → HeyGen rejects it; split into a shorter script or trim
  before sending.
- `status: failed` → read `data.error` and say exactly what it says; don't guess.
- No API budget approved yet → Ken's browser tools can drive heygen.com's own web UI as
  a fallback (upload a script, pick an avatar, download the result by hand), but that's
  slower and needs a human to be present for the HeyGen login — prefer the API.

## What "Hyperframes" turned out to be

The name that prompted this recipe ("hyperframes") is real, but it isn't HeyGen's avatar
API — it's a separate, much heavier HeyGen product: a code-driven video/motion-graphics
*composition* toolkit (Vercel Skills Format, its own CLI, template registry, and render
pipeline — closer to a Remotion competitor than to "turn a script into a talking video").
It's not bundled here: it pulls in its own toolchain outside anything Ken already
depends on, and it solves a different problem (building animated compositions in code)
than the one that actually matters for a recurring content job (a consistent presenter
saying today's update). See [`skills/README.md`](../skills/README.md) for the Claude
Code skills that *are* bundled.
