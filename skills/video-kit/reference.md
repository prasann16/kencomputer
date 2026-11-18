# ElevenLabs: prices, limits, plans (checked Nov 17 2026)

On hosted Ken none of this is said to them: it runs on Ken's own account through the host (`/box/eleven`), and the
only number that matters is what's left of their monthly allowance (`eleven.py check`). The table is for their own
key, and for knowing the limits.

API usage is billed in US dollars; on a subscription it comes out of the plan's included credits first. Prices
change: `eleven.py estimate` uses the table in `scripts/eleven.py`, and `eleven.py check` shows what's left.
Sources: https://elevenlabs.io/pricing/api , https://elevenlabs.io/pricing , https://api.elevenlabs.io/openapi.json

| Feature | Command | Endpoint | About | Limits |
|---|---|---|---|---|
| Dubbing v1 | `dub` | `POST /v1/dubbing` (deprecated, still served) | $0.50/min ($0.33 with watermark) | 3 GB file, 32 speakers, 3 jobs at once |
| Dubbing v2 | `dub --v2` | `POST /v1/dubbing/project` | $2.20/min per language | same; signed output links last 1 hour |
| Text to speech | `tts` | `POST /v1/text-to-speech/{voice}` | $0.08 / 1k chars ($0.04 Flash) | 10k chars a call (v3: 5k, Flash: 40k) |
| Instant voice clone | `clone` | `POST /v1/voices/add` | included in Starter+ | ≥10 s total, 1–2 min best; slots: Starter 10, Creator 30 |
| Voice changer | `changer` | `POST /v1/voice-changer/{voice}` | $0.12/min | 5 min a file |
| Voice isolator | `isolate` | `POST /v1/audio-isolation` | $0.12/min | 500 MB, 1 hour |
| Sound effects | `sfx` | `POST /v1/sound-generation` | $0.12/min | 0.5–30 s |
| Music | `music` | `POST /v1/music` | $0.15/min, paid plans | up to 5 min |
| Speech to text (Scribe v2) | `captions` | `POST /v1/speech-to-text` | $0.20/hour | 5 GB, 15 hours |
| Voice design | `design`, `design-save` | `POST /v1/text-to-voice/design`, `POST /v1/text-to-voice` | not published (short previews) | 100–1000 char preview text |
| Usage | `check` | `GET /v1/user/subscription` | free | |

Plans (monthly): Free $0 (10k credits, no cloning, dubs watermarked, no commercial use), Starter $6 (30k, cloning,
commercial use), Creator $22 (121k, professional clone), Pro $99 (600k).

Consent: ElevenLabs' use policy (https://elevenlabs.io/use-policy) forbids cloning a living person without
explicit, informed consent, and anyone under 18. Ken goes further: their own voice only, their yes in chat recorded (who, when, which files).

Not covered here on purpose: the official ElevenLabs MCP server (github.com/elevenlabs/elevenlabs-mcp) has the same
voice tools but no dubbing, keeps files only under one base folder, and its README says the local server is no
longer maintained in favour of a hosted OAuth one. The helper is one stdlib file with no install, so it works the
same on every Ken.
