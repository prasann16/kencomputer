# Designing it: graphics, text and captions in Remotion

Everything drawn on the video is made here: captions, the hook, titles, lines of text, graphics. Whether a video
needs graphics, which ones, how many, and how they look and move is your call, decided for this video and what it's
saying (their style note wins where it says something). Show the real thing when they mention an app, tool or site
(its real icon or screen).

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
- One that never moves (a shade behind the captions, a frame, a corner logo) gets `"still": true` in props.json:
  it's drawn once as one image, so a long one doesn't make every later fix redraw the whole reel.
