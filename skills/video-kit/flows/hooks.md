# Hooks: the first 3 seconds

No API key needed. What the evidence says: speech starting in the first 3 seconds holds viewers better than a
music-only open (+11.7% at 3 s, +24.7% at 10 s on Facebook Reels, Emplifi, Mar 2026). Most other "hook science"
online is vendor claims. Never promise a video will go viral.

Every hook has three layers; write all three:

1. **Visual**: what's on screen in frame one (their face mid-sentence, the product, the result, a screen).
2. **Spoken**: the first line they say. It has to say something, not "so today I want to talk about".
3. **On-screen text**: 3 to 7 words that add to the spoken line, not repeat it.

## "Write hooks for this video"

1. Transcribe it (`transcribe_media`) and find the strongest line anywhere in it: a number, a result, a contrarian
   take, a mistake, a question the viewer has.
2. Write 5 hooks, each with its three layers, each a different type (result first, mistake, contrarian, question,
   story mid-action). Their words where possible; every claim true to the video.
3. Recommend one. On their pick, the video skill re-cuts the opening (start on that line, hook text on screen).

## "Find what works" (their account and their niche)

1. **Their own posts:** their last 20 to 30 videos with views and followers (YouTube via the connected account,
   Instagram Insights on a Business/Creator account, or their public profile in the browser). Rank by views per
   follower, not raw views. Transcribe the first 5 seconds of the top 5 and the bottom 5 (`transcribe_media` on a
   download or screen recording of their own videos).
2. **Their niche this week:** read the top 10 to 20 public posts in their niche the way a person browses (YouTube
   search, the public pages), never logged in scraping, never at volume. Note each opener's three layers.
3. Tag each opener (type, first words, visual, text) in `videos/hooks/<date>.md`. Tell them in 3 lines: what their
   winners have in common, what their losers do, and the 2 hook types winning in their niche, with one example each.
4. Keep the file: next time, compare with it.

## "Why did this one flop?"

Look at the first 3 seconds only, first: was speech in the first second, did the first line say something, was
the on-screen text readable and outside the app buttons, did the picture change? Name the one biggest problem
and a re-cut that fixes it. Don't invent reasons the numbers don't show.
