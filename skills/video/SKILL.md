---
name: video
description: Turn someone's talking video into a finished reel (Instagram, TikTok, Shorts, LinkedIn): tightened in their own order, with a hook, word-by-word captions, graphics and explainer animations, in their style. Use when they send a video to edit, ask for a reel, captions, infographics or motion graphics on a video, or want changes to a reel made earlier.
---

# Talking video → finished reel

They ramble to their phone. You hand back a reel they'd post untouched: it hooks in the first two seconds, says
one thing well, and **shows** what they say with graphics, not just captions over a talking head. They don't need
to know anything about editing. Work in `videos/<short-name>/` in your workspace; that folder is the project, so
changes later pick up where you left off. Never change their original file.

`<ken>` below is the exact command in your instructions.

## 0. Their style first

Read your memory for their video style (a note like `memory/video-style.md`). If there isn't one yet, this is
their first video: ask ONE question before you start, "Any style you want? Send a reel you like, your brand
colours or logo, or say 'surprise me'." If they send a reel, grab a few frames of it with ffmpeg and match its
look (fonts, colours, captions, how graphics look and move). If they say "surprise me", or your instructions
say not to ask, design the look from their video (see "The look" in step 3). After they're happy with the first video, write
the style note: fonts, accent colour, caption size, what graphics they like, and every rule they gave you ("no
emojis", "keep my jokes"). Every correction they make on any video goes into that note right away, so they never
say it twice.

## 1. Listen

Copy the video into `videos/<short-name>/` as `source.<ext>`. Run `transcribe_media` on it: you get the
transcript and `source.words.json` with every word's timing. Fix words Whisper mishears in `source.words.json`,
especially names and product names (check your memory for the right spellings). Then:

    <ken> --video-cut videos/<short-name>

prints the transcript with every word numbered and the pauses marked. Read all of it before choosing anything,
and write two lines in `story.md`: what the video is about in one sentence, and the hook text you'll show (3 to 7
words that add to their first line rather than repeat it).

## 2. Tighten it

They already told the story, in their order. Your job is to make it tight, the way a good creator edits their own
video: **keep what they said, in the order they said it, and take out everything that doesn't belong.** Never move
lines around or stitch sentences from different places together: that's what makes a reel feel chopped up and
confusing.

Take out:

- **The warm-up**: "so I want to talk about", "hey guys", restating what they're about to say. Start at the first
  line that actually says something; that's the hook, and the hook text (step 3) does the rest.
- **Retakes and false starts**: when they say something twice, keep the last good take.
- **Tangents**: an aside that doesn't serve the point. Only when the reel still makes sense without it.
- **Repeats**: when they make the same point twice, keep the better one.
- **Dead air**, and "um", "so yeah", "you know", "right" when they sit on their own between pauses.
- **The drawn-out ending**: end on their last strong line or their call to action, not "so yeah, peace".

Keep:

- **Every sentence whole.** A sentence starts where they started it and ends where they finished it. Never start
  a part on a dangling "and", "but" or "so" that leads nowhere, never end on "and" or a comma.
- **Small fillers inside a flowing sentence.** Cutting those makes it choppy and rushed.
- **Their voice, pace and jokes.** The tool already shortens long pauses to a breath.

Aim for about 60 seconds, and never more than 90, unless they ask for a length. Once the waste is gone that's
usually 40 to 70% of the original. If it's still long, drop a whole tangent, a repeated point or backstory the
viewer doesn't need, never half a thought. Cut at `--speed 1.1` unless they already speak fast or want their real
pace: it tightens the reel without sounding sped up.

Pick the words to keep as numbered ranges, in the order they were said, and cut:

    <ken> --video-cut videos/<short-name> 12-40,45-80,96-130 --speed 1.1

It finds where their voice really starts and stops, so no word gets clipped, and holds a second after the last
word so the ending settles. It writes `cut.mp4` (vertical 1080x1920) and `props.json` with every kept word timed on
the new video, and prints the reel's script with `/` at each cut.

**Read that script as a viewer would.** Does it follow what they said, start on a line that says something, and
end on a strong one? Does every sentence finish? Does anything refer to something you cut ("like I said", "that
one")? Fix the ranges and cut again until it reads like they said it that way.

**Don't move on while it warns that a cut lands mid-speech**: that cut will sound chopped. For each one, keep the
next word too (often a "yeah" or "so" they ran straight into) or end the range a phrase earlier, then cut again. If
a warning survives two tries, accept it.

## 3. Design it

**Design this reel for this person, this topic and this vibe**, not from a template and not from your last reel.
Their style note wins when they have one. Otherwise look at a few stills of the cut and decide like a designer
would: who they are, the mood of what they're saying, where the open space in the frame is, and what would look
made for exactly this video. Two people's reels should never look alike, and two of the same person's reels on
different topics can differ too.

**The look.**

- **Colours from what's on camera.** Run `<ken> --video-colors <folder>`: it lists the colours in the footage
  (their clothes, the room, plants, sky, water), most present first, and suggests an `accent`, `paper` and
  `ink` from them. Look at a still too: an accent from something that belongs to them (a cap, a jacket, a brand
  colour, the plants behind them) beats a big empty sky. Keep it bright enough to read over the video.
- **Type that fits the person and the topic.** Three faces are bundled: `Inter` (every weight), `Montserrat`
  (black only) and `Serif` (an elegant serif, with italic). Choose `font` (captions), `accentFont` (the hook, the
  emphasized words and `*starred*` words), `headingFont` (headlines on library graphics), `captionWeight`,
  `captionSize`, `uppercase` and `hookSize` for this reel; nothing is the default style.
- **Texture is a choice.** `grain` and `paperTexture` are on unless you turn them off: keep them when the reel
  should feel printed or filmic, turn them off for a clean, flat look.
- **Say what you chose** in a line ("the green from your jacket, big tight sans captions, no grain"), so they can
  ask for another.

**Plan the moments.** Read the cut words in order and decide what the viewer should *see*:

- **Hook text** (`hook`): over the first 2.5 to 3.5 s, from `story.md`. Star the one word that matters. Or design
  your own hook graphic for those seconds and leave `hook` out.
- **Emphasis** (`words[i].emph = true`): about one word per sentence, the word they'd stress out loud. It's set
  larger in the accent. Never two in a row.
- **Push-in** (`zooms`): `{start, end}` on the one line that carries the reel, at most one. The camera slowly
  pushes in while they say it.
- **End note**: over the last 2 to 2.5 s, while they say their last line (never an extra end screen: reels loop,
  and dead time at the end loses viewers). Default text "Follow for more on *<topic>*", or their own sign-off from
  your memory. Skip it if they already end on a call to action, or don't want one.
- **Graphics**, where a picture says it better than a face. For each one, first decide what would show *this*
  idea best in *this* reel's look. Then use a library component if it fits as it is, adapt one, or write a new one
  (below). Making a graphic for the reel is normal, not a last resort: the library is a toolbox, not a menu.

Craft rules, whatever the look:

- **Restraint.** Calm beats busy: the story carries the reel, the design only supports it. A graphic every 8 to
  15 seconds where it genuinely helps; a 45-second reel usually has 2 to 4. More minimal than you think.
- **One idea on screen at a time**, never two moves at once. Readable in two seconds. On screen 3 to 5 s when it
  covers them, 2 to 4 s over the video.
- **Timed to the words**: it comes in as they say the thing it shows.
- **Their face for what matters most**: the hook and the most personal moment stay on their face, no graphics.
- **The real thing**: when they mention a tool, app or site, show its real icon or real interface (a screenshot,
  or a Mac app's icon from `sips -s format png /Applications/<App>.app/Contents/Resources/<icon>.icns --out
  icon.png`), never a generic drawing.
- **Never**: stock icons, emojis as decoration, gradient boxes, typewriter text, neon, shaking or bouncing text.
- **Safe zones**: the apps put their own buttons over the top 250 px, the bottom 480 px and the right 140 px.
  Keep text and anything that matters out of them.

Add to `props.json` (keep `words`, `cuts` and `framing` from the cut, and fix misheard words in `words`). The
shape, with placeholders:

```json
{
  "hook": "<3 to 7 words with one *starred* word>",
  "hookSeconds": 3,
  "zooms": [{"start": 30.5, "end": 35.3}],
  "graphics": [
    {"type": "<a library type>", "start": 8.4, "end": 11.6, "props": {"<prop>": "<value>"}},
    {"type": "<your own graphic>", "start": 31.0, "end": 35.3, "placement": "over", "props": {"<prop>": "<value>"}}
  ],
  "style": {"accent": "<hex>", "paper": "<hex>", "ink": "<hex>", "font": "<font>", "accentFont": "<font>"}
}
```

The library (use or adapt only when it fits):

| type | placement | props |
|---|---|---|
| `title` | full | `kicker?`, `text` |
| `stat` | sheet | `value` (number counts up, or text), `prefix?`, `suffix?`, `label`, `note?` |
| `list` | sheet | `title?`, `items` (2 to 4, short), `marker?` (`numbers` / `checks` / `bullets`) |
| `compare` | sheet | `title?`, `left` / `right`: `{label, items}` (left is the old way, crossed out) |
| `bars` | sheet | `title?`, `unit?`, `bars`: `[{label, value}]` (up to 4) |
| `quote` | sheet | `text`, `by?` |
| `chat` | full | `title?`, `name?`, `messages`: `[{from: "me" or "them", text}]` |
| `picture` | full | `src` (a file in the folder), `caption?` |
| `nameTag` | over | `name`, `role?` |
| `callout` | over | `text`, `emoji?` |
| `punch` | over | `text` (two to five words) |
| `eaten` | sheet | `title?`, `eaters` (2 to 3 names), `apps`: `[{label, icon?}]` (4 to 6) |
| `obsolete` | sheet | `title?`, `items` (3 to 4) |
| `focus` | sheet | `big` (the vague thing), `small` (the sharp one), `label` |
| `flow` | sheet | `title?`, `input`, `worker`, `output`, `time?` (5 to 6 s long, it plays in steps) |
| `timeline` | sheet | `title?`, `label?` |
| `browser` | over | `url` |

**sheet**: paper slides up over the lower half and they lift to stay in view. **full**: paper wipes over the whole
screen (the voice keeps playing). **over**: floats on the video. Each graphic can set `placement` to override, and
`hideCaptions` (captions step aside for sheet and full graphics by default).

`style`: `font` (captions and body: `Inter` or `Montserrat`), `accentFont` (hook, emphasis, starred words, big
numbers and quotes; `Serif` by default), `headingFont` (headlines on graphics; `Serif` by default), `hookSize`
(default 128), `accent`, `textColor` (captions), `paper` / `ink` (graphic colours), `captionSize` (default 62),
`captionWeight` (default 650), `uppercase`, `captions` (false turns them off), `wordsPerPageMs` (default 1100),
`zoomCuts` (a slight punch-in on some jump cuts, default true), `grain` (film grain and vignette, default true),
`paperTexture` (fibres on paper, default true).

**Writing a graphic.** The folder's `src/` appears with the first `--video-stills` (run it before `props.json`
names your graphic). Add a component to
`src/graphics/` (the helpers are in `layout.tsx` and `anim.ts`: `Area`, `Heading`, `Rich`, `accentType`,
`useEnter`, `rise`, `pop`, and `useTheme` for the reel's colours and fonts; `@remotion/rough-notation` draws
hand-drawn marks), register it in `src/graphics/index.ts` with its placement, and use it in `props.json`. Then:

- Animate only from `useCurrentFrame()`: no CSS transitions, `Math.random` or clock time, or frames come out
  different each render.
- Take its text from props, so a fix is a `props.json` edit. An "over" graphic positions itself inside the safe
  zones; a sheet or full one lays out in `Area`.
- A picture it shows is a file in the folder passed as its `src` prop (only those get bundled), one per graphic.
- Run `npx tsc -p .` in the folder and fix every error before checking frames.

## 4. Check frames before rendering

Checking frames takes seconds. Give all the times in **one** command (never run renders or stills in parallel:
each opens Chrome and uses about a gigabyte):

    <ken> --video-stills videos/<short-name> 1.5 10.5 24 38.2

then look at `checks/sheet.jpg` (all of them side by side, the safe zones tinted red). Check the hook, each
graphic and an emphasized caption. Look for: text that's cut off, too long or in a red zone, a graphic covering
their face at the wrong moment, captions colliding with an over-the-video graphic (move it or set
`hideCaptions`), colours that clash with the footage. Ask yourself: would a good editor be proud of this frame, or
does it look like a template, or like your last reel?

The first time, this sets up Remotion (a one-time download, a few minutes): tell them in one line.

## 5. Render and check

    <ken> --video-render videos/<short-name>

makes `reel.mp4`, ready to post. Remotion draws only the captions and graphics, and keeps each by what's in it;
ffmpeg lays them over the footage in one pass. The first render takes a few minutes, so run it as a background
job; a fix later redraws only what changed and takes a minute or two. A style change (caption font or colour)
redraws everything, so settle those with stills first. `--plan` says how much a change would redraw without
rendering. The render checks its own result and, if that fails, draws the whole reel in Chrome instead (slower,
always right). Then:

- Duration: `ffprobe -v error -show_entries format=duration -of csv=p=0 videos/<short-name>/reel.mp4`
- Frames at the start, at two or three graphics and at the end (`ffmpeg -v error -ss 9.5 -i reel.mp4 -frames:v 1
  f.jpg`), and look at them.
- Re-read the cut words in order: does the story flow, is any sentence chopped mid-thought?

Fix what's wrong, check the fix with stills, then render again. Don't send something you wouldn't post. Then send
`reel.mp4` and say where it's saved, in one short paragraph: "Here's your reel, 52 seconds. I opened with the line
about quitting your job, cut the two retakes, and added graphics for the 300% number and your three rules. Want the
captions in your brand orange?"

## Changes later

"Make the captions bigger", "change the graphic at 0:40", "drop that bit": open the reel's folder and change only
what they asked in `props.json`, or cut again with the changed ranges (it keeps your word fixes and emphasis, and
everything else in `props.json`; move graphics after a change by the same amount). Check stills, render, check.
Note every correction in their style note, since it probably applies to future reels too.
