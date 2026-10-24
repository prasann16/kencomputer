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

Copy the video in as `source.<ext>`. Run `transcribe_media` on it: you get the transcript, an `.srt`
and `source.words.json` with every word's timing. Fix words Whisper mishears, especially names and
product names (check their memory for the right spellings).

## 2. Cut

Make a cut list of source-time ranges `[[start, end], ...]` that keeps everything they meant to say and
drops dead air, ums, false starts and retakes (when they repeat a line, keep the last good take). Keep
the length they asked for; don't shorten their message to hit a number. Then cut and join with ffmpeg
(trim + concat, `loudnorm` on the audio), vertical 1080x1920 at 30 fps, into `public/cut.mp4`.
Shift the word timings onto the cut timeline (subtract the gaps) so captions line up with `cut.mp4`.

If they only want cuts and plain captions, you can stop here: burn in captions with ffmpeg's
`subtitles` filter and skip Remotion.

## 3. Cards and captions (Remotion)

Remotion isn't installed until it's first needed. If `--video-new` says it's setting up, that's the
one-time download (a few minutes): tell them in one line, and run it as a background job if you're in
the chat.

    <ken> --video-new videos/<short-name>        (the exact command is in your instructions)

That folder now has `reel.json`, `src/`, `public/`. Put `cut.mp4` in `public/`, then fill in
`reel.json`; don't rewrite the code for a normal video:

- `duration`: length of `cut.mp4` in seconds. `cuts`: the cut boundaries on the cut timeline (each
  other cut gets a small punch-in zoom). `words`: the shifted word timings.
- `style`: from their style note (`font`: Montserrat, Inter, Poppins, Bebas Neue or Instrument Serif;
  `accent`; `captions` top/size/uppercase/perPage).
- `scenes`: a card every few seconds while they make a point, timed to the words that say it. Each has
  `from`/`to` and any of: `kicker` (small label), `title` (`*word*` for the accent colour), `text`,
  `chips`, `bars`, `checks` (lists of `{label, at}` that pop in as they say each one), `big` (a URL or
  number), `image` (a file in `public/`). Short words on cards; the captions carry the sentence.
- `end`: a closing card (their handle, site or call to action), if they want one.

Only when a video needs something these cards can't do, add a component to `src/Reel.tsx`.

## 4. Preview, then render

Before the full render, show them 3-4 stills: `npx remotion still src/index.ts Reel still.png
--frame=<n>` at a few moments with cards. Send them and ask "like this?". Change what they say
(and note it in their style). Then render in the project folder, as a background job if it's long:

    npx remotion render src/index.ts Reel out.mp4

Re-encode for upload: `ffmpeg -i out.mp4 -c:v libx264 -crf 21 -maxrate 10M -bufsize 20M -c:a aac
-b:a 192k -movflags +faststart reel.mp4`. Watch it: grab frames from the start, middle and end
and look at them. Then send `reel.mp4` and say where it's saved.

## Small fixes later

"Make the captions bigger", "change the card at 0:40": edit `reel.json` and render again. A change to
the cut means redoing step 2 and shifting the word timings again.
