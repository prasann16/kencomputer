---
name: viral-content
description: Find what's working for creators in their niche (travel, lifestyle, food) and turn it into their next reel. A trend board of the reels beating their account's usual views and why, a hook bank in the creator's own voice, and a full reel plan (script, shot list, edit notes) the video skill can cut. Use when a creator asks what's trending or going viral, for hooks, reel or content ideas, "what should I post", "why did this reel blow up", "make one like this", or sends reels they like.
---

# What's working → their next reel

A creator asks "what should I post?" and gets proof, not vibes: the reels in their niche that beat their own
account's usual views this week, what each one does in its first 3 seconds, hooks rewritten in their voice, and a
plan for their next reel that you can edit from their own footage. Everything lives in `creator/` in your
workspace: `creator/profile.md` (who they are), one folder per board (`creator/boards/<date>/`), and
`creator/hooks.md` (their hook bank, added to every week).

`viral.py` is in this skill's folder. Run it with the Python in your `<ken>` command (it has faster-whisper);
any Python 3.10+ works without transcripts. `<v>` below means that command.

## 0. Know the creator (once)

Read `creator/profile.md` and their video style note (`memory/video-style.md`). If there's no profile, ask ONE
question and save the answer: "Send me 3 to 5 accounts you look up to, or links to reels you wish you'd made."
The profile holds: niche and places (e.g. travel, Madeira, Iceland), platforms, the accounts they look up to,
how they talk (5 of their real captions or lines, quoted), what they never do ("no dancing", "no face"), and
their footage folder (Dropbox, Drive). Fill what you can from what they already gave you; don't interview them.

## 1. Find reels worth studying

Best source first. Stop when you have 8 to 12 good ones.

1. **Links or files they send.** Always the best signal: they chose them. `<v> fetch <board> <url or file>...`
2. **Accounts they look up to.** `<v> find <board> --accounts handle1,handle2 --per-account 3` lists each account's
   recent short videos scored against that account's own median views (an outlier score). YouTube handles work
   publicly. For Instagram accounts, use the Instagram connection's business discovery if one is set up (it
   returns view counts for business and creator accounts); never log in to Instagram in a browser to scrape it.
3. **Their own best posts.** If their Instagram, TikTok or YouTube is connected, their top 5 by views (and saves,
   shares, average watch time where the API gives them) show what already works for *them*. Study these first.
4. **Hashtag pages, last.** `<v> find <board> --niche travel --tags iceland,solotravel` reads public YouTube Shorts
   hashtag pages. Noisy: expect reuploads, AI spam and other languages ("madeira" is also Portuguese for wood).

`<board>` is `creator/boards/<YYYY-MM-DD>`. Then `<v> fetch <board> <the urls you picked>`. A link that
needs a login (most Instagram reels) is skipped: ask them to send the file or a screen recording instead. Never
use their cookies or account to download.

## 2. Measure

`<v> analyze <board>` writes `signals.json` in each reel's folder: length, cuts per 10 s, average shot, first cut,
cuts in the first 3 s, rough on-screen text (OCR), speech seconds and the first 3 s of words, views, likes,
outlier score. Plus two pictures to look at: `hook.jpg` (frames at 0, 0.5, 1, 1.5, 2, 3 s) and `sheet.jpg`
(12 frames across the whole reel). Whisper invents words over music: trust `talking`, not the text, when
`speech_seconds` is low.

## 3. Judge each one (you, by looking)

Open `hook.jpg` and `sheet.jpg`, read the transcript and caption, then fill `judged` in `signals.json`:

- `hook_type`: what carries the first 2 s: **voice** (a spoken line), **text** (on-screen words), **visual** (an image
  that needs no words), or a mix ("voice + text"). Say what kind: superlative, open loop, POV, question, number,
  contrarian, "things nobody tells you", identity ("as a European").
- `hook_text`: the hook line as said or shown. `hook_why`: one line on why it stops the scroll.
- `format`: tour, list, POV skit, reveal, explainer, challenge/journey, talking head, cinematic montage + text,
  day in my life, before/after, things nobody tells you. Add the detail in brackets.
- `text_style` (word-by-word captions, boxed title, all caps, one highlight colour), `audio` (voice, trending
  sound, music bed), `cta` ("comment X", a question, none).
- `why_it_worked`: one or two lines, specific to this reel. `steal_this`: the move they can copy, as a template.

**Drop** before the board: reuploads and compilations of other people's clips, AI-generated spam, anything filming
strangers' bodies or people who didn't agree to be filmed, off-niche, and languages they don't post in. Delete
the folder. A small account at 100x+ is usually a fluke or a reupload: check before ranking it first.

## 4. The trend board

`<v> board <board> --niche travel` writes `board.md`, `board.html` and `board.png` (a picture of the board: it
shows in the chat and on their phone). Send `board.png` with three lines: what's working this week (the summary
line), the one pattern most worth copying for them, and the offer: "Want hooks for your next reel, or a full
plan?"

## 5. The hook bank

Add 10 hooks to `creator/hooks.md`, newest first, under a dated heading. Each hook: their version (in their
voice, about their places and footage, true for them), the pattern it borrows and the reel it came from, and
which of their trips or clips it fits. Their voice comes from their captions and transcripts in the profile:
same words, same energy, same emoji habits, no hype words they wouldn't use. A hook claims only what their footage
can show ("the only…", "nobody tells you…" must be true).

## 6. Plan their next reel

When they pick a hook or a format, write `creator/reels/<short-name>/plan.md`:

- **The idea** in one sentence, the format, the reel it learns from, the length (match the board: usually 15 to
  45 s for montage + text, 30 to 60 s for voice-over tours).
- **Hook**: the first 2 s, shot and words. **Script**: voice-over or on-screen text, line by line with seconds.
- **Shot list**: every shot, in order, with its length: from their footage folder when it has the shot (file
  name and second), or "to film" with how (angle, movement, 3 to 5 s, vertical).
- **Edit notes**: cuts per 10 s from the reference, text style, captions, sound (trending sound or their voice;
  never claim a sound is trending without a source), colour (keep their look).
- **Caption** for the post in their voice, a CTA, 3 to 5 hashtags.

Show them the plan in a few lines and wait for their yes. On yes, the edit is a background job that follows the
video skill (talking footage) or cuts their clips to the shot list with ffmpeg (montage). **Never post.** The
finished reel and caption go to them; posting needs their yes for that exact post.

## 7. Weekly

After the first board they like, offer once: "Want a fresh board every Monday morning?" On yes, add a job to
`jobs.json` (say "weekly-trends", Monday 09:00) that runs steps 1 to 5 on their accounts and sends the board with
5 new hooks. Report nothing new as NOTHING_TO_SAY.

## Limits (say them plainly when asked)

- Public counts only, read when fetched. Instagram view counts for other accounts come only through a connected
  business or creator account (business discovery); hashtag search gives no views.
- TikTok trends (Creative Center) need a TikTok login past the top few: link them there, don't scrape it.
- Downloading a public reel they sent, to study it once, is what yt-dlp does; never bulk-download, never
  republish anyone's video, never copy a reel shot for shot: borrow the structure, not the content.
