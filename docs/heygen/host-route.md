# `/box/heygen`: HeyGen on hosted Ken (proposal, not built)

The heygen skill (`skills/heygen`) already speaks to this route. Until ken-hosted has it, a hosted computer with no
`HEYGEN_API_KEY` gets one line: "Ken's built-in HeyGen isn't switched on for this computer yet". It is the same
design as `/box/eleven` (ken-hosted#74, `src/eleven.ts`), so most of it is a copy.

## What the computer sends

- `<host>/box/heygen/<HeyGen v3 path>`, where `<host>` comes from `KEN_INCLUDED_AI_URL` (`…/ai` → `…/box/heygen`)
  or `KEN_HEYGEN_URL`, with `Authorization: Bearer <KEN_INCLUDED_AI_TOKEN>` (or `KEN_HEYGEN_TOKEN`) and
  `User-Agent: Ken`. No HeyGen key is ever on the computer.
- Their own `HEYGEN_API_KEY` in Brain always wins: then nothing goes to the host at all, and a key that runs out of
  credits never falls back to it.

| Call | What the host does |
|---|---|
| `GET /box/heygen/quota` | `{"features": {"avatar_video": {"left": 240, "unit": "seconds", "limit": 300}}, "resets": "2026-12-01"}` |
| `GET /v3/avatars/looks`, `GET /v3/avatars` | sent on with `ownership=private` forced, filtered to this person's groups (`heygen_avatars`) |
| `GET /v3/models/audio/voices` | `type=private` filtered to this person's voices; `type=public` passed through |
| `POST /v3/videos` | checks the avatar (and voice) are this person's, reserves `X-Ken-Seconds` from `avatar_video`, records `X-Ken-Consent`, sends it on, remembers `video_id → user` (`heygen_jobs`) |
| `GET /v3/videos/{id}` | only for the person who made it; on `completed` corrects the reservation to the real `duration`, on `failed` gives it back |
| anything else | refused |

Errors are `{"error": "<one plain sentence>", "code": "quota" | "heygen_down" | "busy" | ...}`; a successful call that
counted something carries `x-ken-quota-left: 180 seconds`. The helper turns `quota` into "Allowance used up: …" and
never mentions money.

## Allowance

Per person, per month, in seconds of avatar video, counted before the call is sent (atomically, as `eleven_usage`
is). Suggested start: paid 300 s a month, free trial 0 (an avatar on the org's account is the one thing here that
could be misused, the same reasoning as clones on `/box/eleven`). Overridable with a `HEYGEN_QUOTAS` var.
`/admin/heygen` shows who used what.

## The open question: whose avatar is on the org's account

On their own key, "their own avatar" is simply `ownership=private` on their account. On the org's account every
person's twin lives in one workspace, so the host has to own the mapping:

1. The person makes their twin through Ken: the host creates the avatar group from footage they send, then
   `POST /v3/avatars/{group_id}/consent` returns HeyGen's own webcam consent page (valid 24 hours), which they
   open and finish themselves. The host stores `group_id → user` and only lists the group once HeyGen says
   `consent_status: accepted`.
2. Nothing else on the org's account (stock presenters, another person's twin) is ever listed or usable.

That creation flow is the real work in ken-hosted, and needs a HeyGen plan that allows API twins. Until then the
honest answer on hosted Ken is "add your own HeyGen key", which the skill already says.
