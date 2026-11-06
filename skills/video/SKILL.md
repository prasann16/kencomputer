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

**Plan first, and wait for their yes.** Every new video gets a full look and a written plan before any editing,
even when they told you exactly what to do: they see what they'll get, fix it in a sentence, and nothing is
rendered on a guess. In the chat:

1. Copy the video into `videos/<short-name>/` as `source.<ext>` and run `transcribe_media` on it (step 1).
2. **Watch it.** Read the whole transcript, then look at the footage: one contact sheet of frames every few seconds
   (`ffmpeg -v error -y -i source.<ext> -vf "fps=1/3,scale=270:-1,tile=6x6" -frames:v 1 checks/source.jpg`, more
   sheets for a long video), and closer frames of anything worth showing (a screen, a product, a gesture). Know who is
   speaking, where, what's in the frame, where the open space is, and what each part says.
3. **Write `plan.md`** in the folder, the whole edit decided:
   - **The story**: what the reel says in one sentence, and how long it'll be.
   - **The hook**: the line it opens on (their words, quoted) and the hook text on screen.
   - **The cut**: what stays and what goes, each part quoted by its first words ("cut the intro 'so today I want to…'",
     "cut the retake of 'it's super cheap'"), in their order.
   - **Every graphic**: the second it comes in, the words it lands on (quoted), what it shows and where on screen.
     The first 20 seconds need something to see too, not only captions.
   - **Captions**: their exact words, and the look (font, colour, where).
   - **The look**: the brief from step 3 in one line, and their style note if they have one.
4. **Send them the plan** in plain words, short: a list they can read in thirty seconds, then "Shall I go ahead? Tell
   me anything to change." **Then stop and wait.** Don't cut, design or render anything before their yes.
5. On their yes (with any changes written into `plan.md`), hand the whole job off as a background job: the folder,
   what they asked, `plan.md`. Say it's running and they can keep talking to you. The job follows `plan.md`: steps 2
   to 5 carry it out, and anything it couldn't do as planned goes in its message at the end.

They can skip this: "just do it", "no need to check", or a style note that says so. A change to a reel already made
("bigger captions", "drop that bit") needs no plan: do it.

**Captions are their exact words.** Never shorten, tidy or paraphrase what they said. Before the cut, read every
word of `source.words.json` once against the transcript and fix what Whisper got wrong: names, product names,
numbers, web addresses ("kencomputer.dev", "24/7"), so the captions match what they say, word for word.

## 0. Their style first

Read your memory for their video style (a note like `memory/video-style.md`). If there isn't one yet, this is
their first video: ask ONE question before you start, "Any style you want? Send a reel you like, your brand
colours or logo, or say 'surprise me'." If they send a reel, grab a few frames of it with ffmpeg and match its
look (fonts, colours, captions, how graphics look and move). If they say "surprise me", or your instructions
say not to ask, design the look from their video (the brief in step 3). The style note holds only what they
asked for: a reel or brand they want matched, their colours or fonts, "keep this look", and every rule they gave
you ("no emojis", "keep my jokes"). Every correction they make on any video goes into it right away, so they never
say it twice. Never save a look you chose yourself as their style: each new reel gets its own look.

## 1. Listen

The video is already in `videos/<short-name>/` as `source.<ext>`, transcribed for the plan (do both now if not):
the transcript and `source.words.json` with every word's timing. Fix words Whisper mishears in `source.words.json`,
especially names and product names (check your memory for the right spellings). Then:

    <ken> --video-cut videos/<short-name>

prints the transcript with every word numbered and the pauses marked. Pick the ranges that carry out the cut in
`plan.md`. The hook text there is 3 to 7 words that add to their first line rather than repeat it.

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

There is no house look. The kit gives you tools, not a style: every choice below is yours, made for **this** footage,
this topic and this person. Two reels should never look alike unless they asked to keep a style (their style note
always wins: use what it asks for and design the rest).

**First, the brief.** Look at a few stills of the cut and run `<ken> --video-colors <folder>` (the footage's own
colours, most present first, with a brighter version of each for text over the video, and a light and a dark tinted
by the room). Then write `design.md` in the folder, five or six short lines:

- **Mood**: what this video feels like (a calm walk in an old city, a fast tech take, a warm kitchen chat).
- **Palette**: 2 to 4 colours taken from this footage (a jacket, a wall, the sky, a brand colour they showed), and
  which is for what. Or against it, on purpose, when the brief says so.
- **Type**: the fonts, from the whole Google Fonts library by name (a grotesk, a condensed, a mono, a slab, a
  display serif, a hand-written face…), the weights, and case. Chosen for this person and topic, not a favourite.
- **Where graphics live**: over the video (in the open space, lower thirds, a corner), full-screen type for one big
  line, a split screen with a crop of the footage, a picture-in-picture, a panel only when it truly helps. Prefer
  graphics that sit on the video to panels covering it.
- **Motion**: how things come in and leave (a wipe, a slide, a soft fade, a cut on the beat), one way for the reel.
- **Texture**: none, or grain, or paper, only if the brief calls for it.

Then build the graphics for that brief. If you remember a reel you made before, check its `design.md` and change
what would make them look alike.

**Plan the moments.** Read the cut words in order and decide what the viewer should *see*:

- **Hook** (`hook`): over the first 2.5 to 3.5 s, from `plan.md`, in your hook type. Star the one word that matters.
  Or design your own hook graphic for those seconds and leave `hook` out.
- **Emphasis** (`words[i].emph = true`): about one word per sentence, the word they'd stress out loud. Never two in a row.
- **Push-in** (`zooms`): `{start, end}` on the one line that carries the reel, at most one.
- **End note**: over the last 2 to 2.5 s, while they say their last line (never an extra end screen). Skip it if they
  already end on a call to action.
- **Graphics**, where a picture says it better than a face: a number, a list of steps, before and after, the real
  thing they mention (its icon, a screenshot), a detail of the footage itself. Each one designed for this reel.

Craft rules, whatever the look:

- **Restraint.** Calm beats busy. A graphic every 8 to 15 seconds where it genuinely helps; a 45-second reel usually
  has 2 to 4. More minimal than you think.
- **One idea on screen at a time**, never two moves at once. Readable in two seconds. Up 2 to 5 s.
- **Timed to the words**: it comes in as they say the thing it shows.
- **Their face for what matters most**: the hook and the most personal moment stay on their face.
- **Readable**: enough contrast with what's behind it (a soft shadow, a box, or a quieter spot of the frame).
- **The real thing**: when they mention a tool, app or site, show its real icon or interface (a screenshot, or on a
  Mac an app's icon from `sips -s format png /Applications/<App>.app/Contents/Resources/<icon>.icns --out icon.png`).
- **Never**: stock icons, emojis as decoration, gradient boxes, typewriter text, neon, shaking or bouncing text.
- **Safe zones**: the apps put their own buttons over the top 250 px, the bottom 480 px and the right 140 px.

**The toolbox.** `src/kit/tools.tsx` (it appears in the folder with the first `--video-stills`) has parts with no
look of their own: `Place` (a spot in the safe zones, or x/y), `Text` (any Google font by name, any size, weight,
colour, case, tracking, shadow, a box behind), `Box` (shapes, outlines, frosted glass over the video), `Pic`
(pictures, screenshots, icons), `Clip` (a crop of the reel's own video: a detail, a split screen), `Mark`
(marker, underline, box, circle or strike, drawn clean or by hand), `Counter`, `Bars`, `Ring`, `Line`. Motion is in
`src/anim.ts`: `useEnter`, `fade`, `rise`, `slide`, `wipe`, `blurIn`, `pop`, `stagger`.

Two ways to use it:

1. **`design` in props.json, no code**: a graphic made of toolbox items, each placed, styled and animated:

```json
{"type": "design", "start": 8.4, "end": 11.6, "props": {"items": [
  {"kind": "text", "text": "Rent went up *41%*", "y": 1150, "font": "<font>", "size": 92, "weight": 800,
   "color": "<hex>", "mark": "underline", "markColor": "<hex>", "enter": "slide-left"},
  {"kind": "clip", "x": 620, "y": 520, "w": 320, "h": 320, "focus": {"x": 0.55, "y": 0.35, "zoom": 2.5}, "radius": 160,
   "enter": "pop", "delay": 0.3}
]}}
```

   Item kinds: `text`, `counter`, `box`, `pic`, `clip`, `bars`, `ring`, `line`. Each takes `at` (`top`, `upper`,
   `middle`, `lower`) or `x`/`y`, `w`, `align`, `enter` (`fade`, `rise`, `pop`, `blur`, `slide-left|right|up|down`,
   `wipe-left|right|up|down`, `none`), `delay` (s), `exitBefore` (s), and the style props of its part (see the top
   of `src/graphics/design.tsx`).
2. **Your own component** in `src/graphics/` built from the toolbox, registered in `src/graphics/index.ts`, used in
   props.json by name, for anything the items can't say (a moving explainer, a custom chart).

`src/graphics/basics.tsx` and `explainers.tsx` (`title`, `stat`, `list`, `compare`, `bars`, `quote`, `chat`,
`picture`, `nameTag`, `callout`, `punch`, `eaten`, `obsolete`, `focus`, `flow`, `timeline`, `browser`) are
**examples** of the toolbox in use. They take every visual choice from your `style`, but each has a fixed layout:
dropped in as they are, reels start to look alike. Read one for ideas, then make this reel's own.

Every graphic floats over the video. `"placement": "sheet"` puts it on a panel that slides up under the speaker (who
lifts to stay in view) and `"full"` on a panel over the whole screen, in `paper` / `ink`; captions step aside for
panels (`hideCaptions` changes that).

`style` in props.json carries the brief's choices (nothing is chosen for you; unset is plain Inter on white):
`font` (captions and body), `headingFont`, `accentFont` (hook, emphasis, starred words, numbers), any Google Fonts
family by name; `fonts` (more families your graphics use); `captionWeight`, `headingWeight`, `accentWeight`,
`accentItalic`; `textColor` (captions and text over the video), `accent` (emphasis, numbers, marks), `paper` / `ink`
(panels); `hookSize`, `captionSize`, `captionY` (captions' bottom edge, px from the bottom; default 610),
`uppercase`, `captions` (false turns them off), `wordsPerPageMs`, `zoomCuts` (slight punch-ins on jump cuts,
default true), `grain` (film grain and vignette, default off), `paperTexture` (fibres on panels, default off),
`marks` (`hand`, `clean` or `none`, default none: how the examples underline and circle).

Keep `words`, `cuts` and `framing` from the cut in `props.json`, and fix misheard words in `words`.

**Say what you chose** in a line when you send it ("the terracotta from the rooftops, a condensed sans in caps, type
straight on the street, no panels"), so they can ask for another.

**Writing a graphic.**

- Animate only from `useCurrentFrame()`: no CSS transitions, `Math.random` or clock time.
- Take its text from props, so a fix is a `props.json` edit. Keep everything inside the safe zones.
- A picture or clip it shows (a screenshot, a photo, a screen recording they sent) is a file in the folder passed
  as its `src` prop (only those get bundled). Trim or speed up a clip with ffmpeg first, and show it with
  `<OffthreadVideo src={staticFile(src)} muted />`. Everything you need is here: don't read the kit's code.
- Run `npx tsc -p .` in the folder and fix every error before checking frames.

## 4. Check frames before rendering

Checking frames takes seconds. Give all the times in **one** command (never run renders or stills in parallel:
each opens Chrome and uses about a gigabyte):

    <ken> --video-stills videos/<short-name> 1.5 10.5 24 38.2

then look at `checks/sheet.jpg` (all of them side by side, the safe zones tinted red). Check the hook, each
graphic and an emphasized caption. Look for: text that's cut off, too long or in a red zone, a graphic covering
their face at the wrong moment, captions colliding with an over-the-video graphic (move it or set
`hideCaptions`), colours that clash with the footage. Ask yourself: would a good editor be proud of this frame, or
does it look like a template? Does it match `design.md`? If they've had a reel before, compare with its
`design.md` and `props.json`: the same colours, fonts and graphics mean start the look again.

The first time, this sets up Remotion (a one-time download, a few minutes): tell them in one line.

## 5. Render and check

    <ken> --video-render videos/<short-name>

makes `reel.mp4`, ready to post. Remotion draws only the captions and graphics, and keeps each by what's in it;
ffmpeg lays them over the footage in one pass. The first render takes a few minutes, so run it as a background
job; a fix later redraws only what changed and takes a minute or two. A style change (caption font or colour)
redraws everything, so settle those with stills first. `--plan` says how much a change would redraw without
rendering. The render checks its own result and, if that fails, draws the whole reel in Chrome instead (slower,
always right). Then:

- One command for the length and a strip of frames from the finished reel, then one look at it:

      cd videos/<short-name> && mkdir -p checks && ffprobe -v error -show_entries format=duration -of csv=p=0 reel.mp4 && ffmpeg -v error -y -i reel.mp4 -vf "fps=1/6,scale=216:-1,tile=8x1" -frames:v 1 checks/reel.jpg

  The stills already checked the design, so this is for what they can't show: the cut, the timing, a graphic
  that's missing. Don't check frame by frame again unless the strip shows a problem.
- Re-read the cut words in order: does the story flow, is any sentence chopped mid-thought?

Fix what's wrong, check the fix with stills, then render again. Don't send something you wouldn't post. Then send
`reel.mp4` in one short paragraph: what you did and one question, as a confident editor would, never how you checked
it (no "I haven't watched it", no "I only checked stills") and never a file path. Videos stay 7 days, so end with
that: "Here's your reel, 52 seconds. I opened with the line about quitting your job, cut the two retakes, and added
graphics for the 300% number and your three rules. Want the captions in your brand orange? It's here for 7 days, so
download it to keep it."

## Changes later

"Make the captions bigger", "change the graphic at 0:40", "drop that bit": open the reel's folder and change only
what they asked in `props.json`, or cut again with the changed ranges (it keeps your word fixes and emphasis, and
everything else in `props.json`; move graphics after a change by the same amount). Check stills of only what
changed, render, check the strip once.
Note every correction in their style note, since it probably applies to future reels too.
