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
   - **Captions**: their exact words.
4. **Tell them the plan** in a few short lines, in your own words: the main moves, not every cut and graphic
   (`plan.md` keeps the detail). Ask if you should go ahead. **Then stop and wait.** Don't cut, design or render
   anything before their yes.
5. On their yes (with any changes written into `plan.md`), hand the whole job off as a background job: the folder,
   what they asked, `plan.md`. Tell them in one short line roughly how long it'll take. The job follows `plan.md`: steps 2
   to 5 carry it out, and anything it couldn't do as planned goes in its message at the end.

Only words that say to skip the plan skip it: "just do it", "no need to check", "don't send me a plan", in this
message or written in your memory. Their editing rules and style notes ("few cuts", "no zooms") are what goes
into the plan, never a reason to skip it. A message that sends the plan and also says the edit is running is wrong:
the job starts only after their reply. A change to a reel already made ("bigger captions", "drop that bit") needs no
plan: do it.

**Captions are their exact words.** Never shorten, tidy or paraphrase what they said. Before the cut, read every
word of `source.words.json` once against the transcript and fix what Whisper got wrong: names, product names,
numbers, web addresses ("kencomputer.dev", "24/7"), so the captions match what they say, word for word.

## 0. Their style first

Read your memory for their video style (a note like `memory/video-style.md`). If there isn't one yet, decide the
look yourself for this video; don't ask about style first. If they send a reel they like, their colours or logo,
match it. The style note holds only what they asked for: a reel or brand they want matched, their colours or fonts,
"keep this look", and every rule they gave you ("no emojis", "keep my jokes"). Every correction they make on any
video goes into it right away, so they never say it twice. Never save a look you chose yourself as their style:
each new reel gets its own look.

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

Every reel gets infographics: that's what makes it more than captions over a talking head. What kind, how many,
and how they look and move is your call, decided on the fly for this video and what they're saying (their style
note wins where it says something). Show the real thing when they mention an app, tool or site (its real icon or
screen).

Also available: a hook over the first seconds (`hook`, star the word that matters), emphasized caption words
(`words[i].emph = true`), a push-in on a line (`zooms`: `{start, end}`).

**Safe zones**: the apps put their own buttons over the top 250 px, the bottom 480 px and the right 140 px. Keep
text and graphics out of them.

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

There are no ready-made graphics: make each reel's own. Lay a graphic out as one flexbox column or row rather than
many items at fixed x/y, so nothing overlaps.

Every graphic floats over the video. `"placement": "sheet"` puts it on a panel that slides up under the speaker (who
lifts to stay in view) and `"full"` on a panel over the whole screen, in `paper` / `ink`; captions step aside for
panels (`hideCaptions` changes that).

`style` in props.json carries your choices (nothing is chosen for you; unset is plain Inter on white):
`font` (captions and body), `headingFont`, `accentFont` (hook, emphasis, starred words, numbers), any Google Fonts
family by name; `fonts` (more families your graphics use); `captionWeight`, `headingWeight`, `accentWeight`,
`accentItalic`; `textColor` (captions and text over the video), `accent` (emphasis, numbers, marks), `paper` / `ink`
(panels); `hookSize`, `captionSize`, `captionY` (captions' bottom edge, px from the bottom; default 610),
`uppercase`, `captions` (false turns them off), `wordsPerPageMs`, `zoomCuts` (slight punch-ins on jump cuts,
default true), `grain` (film grain and vignette, default off), `paperTexture` (fibres on panels, default off),
`marks` (`hand`, `clean` or `none`, default none: how the examples underline and circle).

Keep `words`, `cuts` and `framing` from the cut in `props.json`, and fix misheard words in `words`.

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
`hideCaptions`). Ask yourself: would a good editor be proud of this frame?

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
