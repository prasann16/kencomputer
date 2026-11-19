# Data sources for the viral-content skill

Researched Nov 19 2026 from official pricing and docs pages (links inline). Nothing was signed up for or paid.

v1 finds outlier reels from accounts the creator names (yt-dlp for Shorts, Instagram business discovery once connected). Three gaps remain:

1. **Discovery:** finding accounts in her niche she doesn't already know
2. **Trending audio:** on Instagram
3. **Real reel numbers at scale:** for Instagram and TikTok (views, likes, comments, shares, the account's usual views)

## Five facts that shape the choice

- **TikTok is banned in India** (since 2020-06-29, [PIB](https://pib.gov.in/PressReleseDetailm.aspx?PRID=1635206)), so it only matters for creators targeting global audiences. Apify's TikTok trends actor refuses `IN` ([page](https://apify.com/automation-lab/tiktok-trends-scraper)).
- **No provider filters Instagram reels by country.** India targeting has to come from Indian hashtags, keywords and creator lists, or from a creator database with location filters (Modash, HypeAuditor, Influencers.club).
- **The only official sources of other accounts' view counts are Instagram `business_discovery` and YouTube.** For business discovery you need the handle. Everything else that reads other people's reels scrapes public data, which Meta's and TikTok's terms forbid. That legal risk sits with the vendor, but it's still Ken's dependency.
- **Instagram does have an official trending audio list:** `GET /ig_audio`, with no search query, returns "trending original sounds or music" ([docs](https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/)). It has no usage counts and no country, includes only audio cleared for third-party use, and needs Facebook Login plus `instagram_content_publish`.
- **Research APIs are off limits for us.** TikTok Research API and Meta Content Library are for academic and non-profit researchers only, non-commercial ([TikTok](https://developers.tiktok.com/products/research-api), [Meta](https://transparency.meta.com/researchtools/meta-content-library)).

## Comparison

Prices are entry level. "Per 1k" is the cost of 1,000 calls or results.

| Source | What it gives | How | Entry price | Free, no card | Limits | MCP | Ken setup |
|---|---|---|---|---|---|---|---|
| **IG Graph `business_discovery`** | Per-reel `view_count` (paid + organic), `like_count`, `comments_count` for any business or creator handle. Enough for a median. No shares, no discovery | Official | Free | Yes | 200 calls × users per hour, app-wide ([rate limits](https://developers.facebook.com/docs/graph-api/overview/rate-limiting)) | Community ([mcpware/instagram-mcp](https://github.com/mcpware/instagram-mcp)) | Her FB login. Serving others needs App Review |
| **IG hashtag search** | top/recent media with likes and comments. **No views, no username**. recent_media covers 24 h only | Official | Free | Yes | 30 unique hashtags per 7 days per user ([docs](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-hashtag-search)). From 2027-02-16, top_media includes Reels, max 200 | — | Same as above |
| **IG Audio API** | Trending music and original sounds (no counts, no region) | Official | Free | Yes | Platform limits | — | Needs Facebook Login + publish scope |
| **YouTube Data API v3** | viewCount, likeCount, commentCount (no shares). `chart=mostPopular&regionCode=IN`. No Shorts filter | Official | Free | Yes | 10k units/day, videos.list = 1 unit, search in its own 100/day bucket ([quota](https://developers.google.com/youtube/v3/determine_quota_cost)) | Composio YouTube toolkit | One org key in ken-hosted, or per VM |
| **Composio** (on Ken VMs) | IG: **own account only** (media, insights, publish). No business discovery or hashtag search ([toolkit](https://docs.composio.dev/toolkits/instagram)). YouTube: search, batch details, most popular | Official APIs | Free 10k calls/mo, $18/seat | Yes | 10k calls/mo | Yes | Already there. Use it for her own insights |
| **ScrapeCreators** | IG: user reels with `play_count`, likes, comments (pageable, so a median). **Trending Reels** (with audio metadata), **Reels by audio ID**, Search Reels. TikTok: profile videos, search, hashtag, song, trending feed by region with shares. YouTube too | Scraping | **$47 for 25k credits** (~$1.88 per 1k). $497 for 500k (~$0.99 per 1k). Pay as you go, credits never expire ([pricing](https://scrapecreators.com/)) | **100 credits + up to 1,200 bonus** | None per account, stay under 500 concurrent | **Official** (`api.scrapecreators.com/mcp`) + CLI | One org key in a ken-hosted proxy |
| **Apify** | `apify/instagram-scraper`: play/view counts, likes, comments, music info, by profile/hashtag/search. Reel scraper adds shares (paid plans). `clockworks/tiktok-scraper`: plays, likes, shares, comments, music | Scraping | IG $2.30 per 1k results on Starter ($19/mo); TikTok from $1.70 per 1k ([IG](https://apify.com/apify/instagram-scraper), [pricing](https://apify.com/pricing)) | $5 credit/mo | 5 concurrent runs free, 32 on Starter. MCP 20 req/s | **Official** (`mcp.apify.com`) | Org token, `maxTotalChargeUsd` per run |
| **EnsembleData** | TikTok: plays, likes, comments, shares, music posts, keyword search by country. IG: reels, hashtag, music posts (play count field unconfirmed) | Scraping | $100/mo for 1,500 units/day ([pricing](https://ensembledata.com/pricing)) | 50 units/day | Daily units | None | Org key |
| **TikAPI** | TikTok only: user posts, hashtag, music, trending discover, `country` proxy for 200+ countries | Scraping ("unofficial") | $29/mo for 300 requests/day ([plans](https://api.tikapi.io/app/plans)) | 5-day trial (card unclear) | Daily caps | None | Org key |
| **RapidAPI tikwm** | TikTok posts, search, hashtag, music, Creative Center sound and hashtag trends | Scraping | Free 300/mo, $59/mo for 3M ([pricing](https://rapidapi.com/tikwm-tikwm-default/api/tiktok-scraper7/pricing)) | 300 requests/mo | Hourly caps | None | Org key. Single-vendor risk |
| **RapidAPI Instagram Looter / Scraper API2** | IG profiles and media | Scraping | Not public without a login | ? | ? | None | Not recommended: no readable terms |
| **Influencers.club** | **Discovery by niche, location, lookalike, AI search**. Posts with views, likes, comments, hashtags, audio. Engagement averages | Public web data + third-party providers | $208/mo (Pro, the cheapest plan with API), ~$0.23 per credit. Discovery 0.01 per creator, posts 0.03 per request ([pricing](https://influencers.club/pricing/)) | **10 credits, keys instant** | 300 req/min | **Official** (hosted + MIT) | Org key |
| **Modash** | Discovery across 380M profiles (location, followers, ER, avg views). Raw API: IG feed, reels, hashtag feed, `audio-info`. 2,438 Indian travel creators on IG ([listing](https://www.modash.io/find-influencers/india/travel)) | Public data, collected automatically | Discovery $16,200/yr, Raw $10,000/yr, bank transfer ([pricing](https://www.modash.io/influencer-marketing-api/pricing)) | App trial only. API needs a sales call | Not public | Mentioned in FAQ, no public repo | Org key, sales |
| **HypeAuditor** | Discovery with 35+ filters, India categories, reports, account media, post search | Third-party public data | API "from $200/month", credit costs not public ([page](https://hypeauditor.com/api-integration/)) | Test key after a sales call | Not public | **Official** (`mcp.hypeauditor.com/mcp`, OAuth) | Org key, sales |
| **Phyllo** | Creator search (470M), public analytics + creator OAuth for private insights | OAuth + public data | Not public (quote) ([pricing](https://www.getphyllo.com/pricing)) | Sandbox | Not public | Claims MCP support, no details | Sales |
| **CreatorIQ** | Enterprise campaign platform. API is mostly your own data, no discovery API | Official + creator OAuth | Enterprise only | No | Not public | No official server | Not a fit |
| **SocialData** | X/Twitter only | Scraping | $0.20 per 1k | — | — | Official | Not a fit |
| **TikTok Research / Commercial Content API, Meta Content Library** | Full public data | Official | Free | — | — | — | **Not eligible** (academic, non-commercial, or ads only) |

Other MCP servers found (official registry and GitHub, Nov 19):
- **Apify MCP** (17k stars, active) is the only mature one.
- **Graph API wrappers:** mcpware/instagram-mcp (87 stars), adelaidasofia/instagram-mcp.
- **Thin wrappers over paid scrapers:** Seym0n/tiktok-mcp (209 stars), HasData, DataLikers, jsonscraper, Xpoz, and registry entries like `ai.trendhaus/tiktok` and `com.shortvidapi/tiktok-instagram-reels-shorts-mcp`.
- **Avoid:** Bob-lance/instagram-engagement-mcp, which needs your Instagram username and password. That risks her account.

## Free credits and startup programs

Checked Nov 19 2026 on official pages. Nothing signed up for.

| Source | Free now | Startup program |
|---|---|---|
| **treg** | $1 once per verified account. Calls at "the provider's own rate, with no markup"; failed calls free; repeat answers from its archive 10% ([pricing](https://treg.to/pricing), [llms.txt](https://treg.to/llms.txt)) | None found. Volume terms by email (llms.txt) |
| **ScrapeCreators** | 100 credits + up to 1,200 bonus (G2 review 1,000, GitHub star 100, survey 100), no card ([pricing](https://scrapecreators.com/#pricing)) | None found. Smallest pack $47 / 25k = $0.00188 a credit; $497 / 500k = $0.00099 |
| **Apify** | $5 usage every month, no card ([pricing](https://apify.com/pricing)) | **30% off Scale ($199/mo) for 6 to 12 months**: live product, < $5M raised, company email (Gmail auto-declined), no lead gen ([startups](https://apify.com/resources/startups)). Only pays off at scale |
| **Bright Data** | 5k records a month free on Web Scraper API, no card; $1.50 / 1k after ([pricing](https://brightdata.com/pricing/web-scraper)) | **AI Startups Program, up to $20K credits**, pre-seed to Series A, AI central to the product ([program](https://brightdata.com/ai/ai-startups-program)). Best credit offer found |
| **EnsembleData** | 50 units a day ([pricing](https://ensembledata.com/pricing)) | None found |
| **SociaVault** | 50 credits; $29 / 6k ($4.83 per 1k) ([pricing](https://sociavault.com/pricing)) | None found |
| **Phyllo** | Sandbox, mock data only ([start](https://www.getphyllo.com/start-building)) | None found, quote only |
| **Influencers.club** | 10 credits ([pricing](https://influencers.club/pricing/)) | None found |
| **RapidAPI IG** | Instagram Looter 150 req/mo, Scraper Stable 20 req/mo ([Looter](https://rapidapi.com/irrors-apis/api/instagram-looter2/pricing)) | None found |
| **SerpApi** | 250 searches a month ([pricing](https://serpapi.com/pricing)) | None found |
| **DataForSEO** | $1 trial, then $50 minimum top-up ([pricing](https://dataforseo.com/pricing)) | None found |
| **YouTube Data API** | 10k units a day; `search.list` has its own 100 calls a day; more via the [quota form](https://support.google.com/youtube/contact/yt_api_form) ([docs](https://developers.google.com/youtube/v3/getting-started)) | n/a |

Cloud programs give compute, **not data**: AWS, Google and Azure credits all exclude marketplace / third-party products ([AWS terms](https://aws.amazon.com/awscredits/), [Google FAQ](https://cloud.google.com/startup/faq), [Azure offer](https://azure.microsoft.com/pricing/offers/ms-azr-0036p/)).
- **AWS Activate Founders:** $1K to $5K, bootstrapped OK ([page](https://aws.amazon.com/startups/credits)). The Bright Data 70% cashback offer is for Portfolio startups only ([offer](https://startups.aws.com/offers/bright-data)).
- **Google for Startups Start:** up to $2K, needs a working MVP, a live site and a domain email ([FAQ](https://cloud.google.com/startup/faq)).
- **Microsoft for Startups:** $200, then $4,800 after verification, no investor needed ([FAQ](https://learn.microsoft.com/en-us/startups/microsoft-for-startups/mfs-faqs)).
- **Cloudflare for Startups Tier 3:** $10K for bootstrapped (< $1M raised), live site and domain email; Workers AI excluded for new joiners from Dec 1 ([page](https://www.cloudflare.com/forstartups/)). Fits ken-hosted, which already uses Cloudflare D1.

**What to apply for:** Bright Data AI Startups (the only real data credits), Cloudflare Tier 3 for ken-hosted, and ScrapeCreators' 1,200 bonus credits. Every program needs the applying company's live site and domain email.

## treg test (Nov 19 2026)

treg (already logged in on Swap's laptop) proxies ScrapeCreators with treg's own key. 4 calls, all HTTP 200, **$0.00752 total** (balance $0.9150 → $0.9075):

| Call | Result |
|---|---|
| `scrapecreators.x.v1-instagram-user-reels` handle=tanyakhanijow | 12 reels with `play_count`, likes, comments, date; `max_id` to page |
| same, page 2 | 12 more. 22 non-pinned reels: median 348k plays, max 10.9M, so outlier scoring works |
| `scrapecreators.x.v1-instagram-reels-trending` | 12 trending reels with plays and audio (`audio_asset_id` / `audio_cluster_id`, title, `is_trending_in_clips`). Global feed, not travel or India |
| `scrapecreators.x.v1-instagram-audio-reels` audio_id from that feed | 12 reels on that audio with plays, `has_more`. `media_count` came back 0, so no usage count |

- **Price:** $0.00188 a call, the same as ScrapeCreators' $47 tier (1 credit). Direct gets 2× cheaper at the $497 tier.
- **Trending audio:** there is no ready list. Build it from the trending feed's audio IDs, then audio reels to count recent plays.
- **Caveat:** treg tagged user-reels calls `routed_tool: treg.instagram.user.reels`, so treg may route a call to another provider. Pin the provider before relying on it. Every query and answer is logged by treg.

**Recommendation:** prototype through treg now (zero setup, $0.0019 a call, the $0.90 left covers ~480 calls). Production goes through a direct ScrapeCreators key in ken-hosted (`/box/social`), so the key, caps and logs stay ours, and the price halves at volume.

## Recommendation

**v1 (free, official, no new vendor)**
- **YouTube:** Data API for Shorts numbers and `mostPopular` with `regionCode=IN`. yt-dlp stays as a fallback.
- **Instagram:** `business_discovery` for the accounts she names, through her own Facebook Login. This gives per-reel `view_count` for the median and the outlier score.
- **Her own reels:** Composio's Instagram insights (views, shares, watch time), already on the VMs.
- **Trending audio:** `/ig_audio`. It only works if she connects with Facebook Login and the publish scope. Otherwise label audio "not available yet".
- **Discovery stays manual:** she names accounts and Ken suggests more from the captions and tags of reels it already fetched.

**v2 (paid, one org key in ken-hosted, per-user allowance like the ElevenLabs proxy in ken-hosted#74)**
- **ScrapeCreators** behind `/box/social/<path>`:
  - Auth: the box's existing `ai_token`.
  - An allowlist: IG user reels, trending reels, reels by audio, search reels, and the TikTok equivalents.
  - Credits reserved per person per month in D1 and refunded on failure, logged at `/admin/social`.
  - The org key never reaches a VM. Example cap: 1,500 credits a month on paid plans, 200 on the trial.
- **Rough cost** (my estimate, assuming 1 credit a call as the docs say for most endpoints):
  - One trend board is about 10 accounts × 3 pages of reels + 10 trending and search calls, roughly 40 credits. That's about $0.08 at the $47 tier, or $0.04 at the $497 tier.
  - 1,500 credits a person a month costs about $2.80.
- **Add Influencers.club ($208/mo) only if** "find creators like me in India travel" turns out to be the feature people ask for. It's the cheapest self-serve discovery API with location filters and an official MCP. Modash and HypeAuditor do it better for India but need sales calls and $10k+/yr.
- **Apify** is the backup if ScrapeCreators breaks or changes terms: the same data at about $2.30 per 1k results, with an official MCP. TikAPI or EnsembleData only if TikTok-heavy (non-India) creators show up.

**Try first: ScrapeCreators.** It's the cheapest pay as you go option ($47 for 25k credits, credits never expire). It has a free tier with no card, an official MCP, and it covers all three gaps from one key: per-reel plays for medians, trending reels with audio, and reels by audio.

### 1-week test plan (free credits only, no card)

| Day | Do | Pass if |
|---|---|---|
| 1 | Run through treg now (see the treg test above), or get the free key (Swap signs up, no card). Add `--source scrapecreators` to `viral.py fetch`: IG user reels for the 10 travel accounts already in the test. Log credits used per call | Plays, likes and comments come back for at least 8 of 10 accounts. Credits per call are known |
| 2 | Compare with ground truth: the same reels via `business_discovery` (her FB login) and the views shown in the IG app | Plays within ±10% of the official `view_count` on at least 80% of reels |
| 3 | Trending Reels + Reels by audio ID: build an "audio rising" list (reels per audio in the last 7 days, median plays) | At least 5 audios she recognises as trending in her niche (Swap or a test creator judges) |
| 4 | Discovery: Search Reels and Popular Search for "india travel", "goa vlog", "solo travel india". Keep accounts with outliers ≥ 3× their median | At least 10 new relevant Indian accounts she didn't name |
| 5 | Wire the official MCP (`api.scrapecreators.com/mcp`) into one Ken VM's tools.json and run the whole board from chat | Board renders end to end from chat, credits per board ≤ 60 |
| 6 | Sketch the `/box/social` proxy in ken-hosted (allowlist + D1 allowance), copying #74 | Design reviewed, no money spent |
| 7 | Write up: accuracy, credits per board, failure rate, gaps. Decide go/no go | Go if days 1 to 4 pass and a board costs < $0.15 |

**Stop if:** play counts are off by more than 20%, more than 20% of calls fail, or their terms forbid reselling access through a proxy. Read their ToS before day 6.
