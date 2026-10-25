---
name: video
description: Turn someone's talking video into a finished reel (Instagram, TikTok, Shorts, LinkedIn) with cuts, word-by-word captions and infographic cards, in their own style. Use when they send a video to edit, or ask for a reel, captions, infographics or motion graphics on a video.
---

# Talking video → finished reel

They send a video of themselves talking. You give back a clean, captioned reel with a few infographic
cards, in their style, without them knowing anything about editing. Work in `videos/<short-name>/` in
your workspace. Never change their original file.

## 0. Their style first

Read your memory for their video style (a note like `memory/video-style.md`). If there isn't one yet,
this is their first video: ask ONE question before you start, "Any style you want? Send a reel you
like, your brand colours or logo, or say 'surprise me'." If they send a reel, grab a few frames with
ffmpeg and copy its look (caption style, colours, card style). After they're happy with the first video,
write the style note: font, accent colour, caption size and position, uppercase or not, what cards they
like, and every rule they gave you ("no emojis", "keep my jokes"). Every correction they make on any
video goes into that note right away, so they never say it twice.

## 1. Transcribe

Copy the video into `videos/<short-name>/` as `source.<ext>`. Run `transcribe_media` on it: you get the
transcript, an `.srt` and `source.words.json` with every word's timing. Fix words Whisper mishears in
`source.words.json`, especially names and product names (check their memory for the right spellings).

## 2. Cut

    <ken> --video-cut videos/<short-name>                 (the exact command is in your instructions)

prints the transcript with every word numbered and the pauses marked. Pick what to keep as word ranges
and cut:

    <ken> --video-cut videos/<short-name> 0-40,45-80      (or `all` to keep every word)

It finds where their voice really starts and stops, so no word gets clipped, shortens long pauses to a
breath, punches in a little on every other part (add `--no-zoom` if they don't want that), and makes
`public/cut.mp4` (vertical 1080x1920) plus `reel.json` with every kept word timed on the new video.
Edit like a person, not a stopwatch:

- Cut between whole thoughts, at the end of a sentence or at a pause. Never in the middle of one.
- Drop false starts and retakes (keep the last good take), dead air, and an "um", "so" or "yeah"
  that sits on its own between pauses.
- Keep small fillers inside a flowing sentence: cutting those makes it sound choppy and rushed.
- Don't tighten for speed; the tool already shortens long pauses. Keep their pace and their jokes.
- When it says a cut lands mid-speech, move that cut to a pause or keep the word, and cut again.

Cutting again rewrites the word timings in `reel.json`; move the cards to match.

## 3. Captions and cards: reel.json

Fill in `reel.json`; don't write code for a normal video:

- `style`: from their style note. `font`: Montserrat, Inter, Poppins, Bebas Neue or Instrument Serif;
  `accent` colour; `captions`: `top` (pixels from the top), `size`, `uppercase`, `perPage` (words at a
  time), `show: false` for none. Fix a misheard word in `words` here.
- `scenes`: a card every few seconds while they make a point, timed to the words that say it. Each has
  `from`/`to` and any of: `kicker` (small label), `title` (`*word*` for the accent colour), `text`,
  `chips`, `bars`, `checks` (lists of `{label, at}` that pop in as they say each one), `big` (a URL or
  number), `image` (a file in `public/`), `top` (pixels from the top; keep cards off their face). Short
  words on cards; the captions carry the sentence. No cards for a plain captioned cut.
- `end`: a closing card `{from, title, subtitle}` (their handle, site or call to action), if they want one.

Only when a video needs something these cards can't do, add a component to `src/Reel.tsx`.

## 4. Render

    <ken> --video-render videos/<short-name>

makes `reel.mp4`, ready to post. Captions are drawn by ffmpeg and each card is rendered once and kept,
so after the first render a change to the captions, the style or one card takes about a minute, not a
whole new render. Never render the whole video in Remotion yourself. The first video with cards sets
up Remotion (a one-time download, a few minutes): tell them in one line, and run the first render as a
background job.

Watch it before sending: grab frames from the start, middle, end and every card with ffmpeg and look at
them. Then send `reel.mp4` and say where it's saved.

## Small fixes later

"Make the captions bigger", "change the card at 0:40", "drop that bit": edit `reel.json` (or cut again)
and run `--video-render` again. Note every correction in their style note.
