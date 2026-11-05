---
name: visual-explainer
description: Answer "explain X", "show me how X works", "draw this", "how do I connect / assemble / set up X" or "what am I looking at" with a picture made in seconds, not a wall of text. A diagram, a labelled 3D drawing, or a step by step guide (PNG, plus a short MP4 for steps), using what they're looking at when there is something (a photo they sent, their screen, a video or link). Follow ups ("make it simpler", "zoom into the fan", "now show step 3") edit the same picture.
---

# Visual explainer: a picture in seconds

They ask how something works, often by voice from their phone. You answer with one crafted picture that explains it
at a glance, plus one or two lines of text. Speed matters: aim for the first picture within a minute of the question.
Each render takes about a second (a step by step MP4 about 7).

`<skill>` below is this skill's folder (`skills/visual-explainer` in your skills folder). Run the renderer with
`python3`, or with the Python from the `<ken>` command in your instructions. It needs only the standard library,
a Chrome or Chromium on the computer and ffmpeg (for the MP4), and it draws in a throwaway profile, never in their
own browser.

    python3 <skill>/render.py new visuals/<short-name> [--3d]     copies a template to visuals/<short-name>/visual.html
    python3 <skill>/render.py visuals/<short-name>                visual.png, 1080x1350 phone portrait
    python3 <skill>/render.py visuals/<short-name> --steps        also step-1.png ... and steps.mp4

Work in `visuals/<short-name>/` in your workspace. That folder is the visual: follow ups edit it, so keep it.

## 1. Get the context (only if there is some)

- **A photo they sent** (in the inbox): open it and look before drawing. Draw *their* thing: their board, their
  error, their chart. Copy it into the folder as `context.jpg`; you may place it in the visual (an `<image>` in the
  SVG) with your labels over it, which is often the best explainer of all.
- **"What's this on my screen" / "explain what I'm looking at"**: take a screenshot only because they asked.
  Linux Wayland `timeout 10 grim visuals/<short-name>/screen.png` (X11: `import -window root`), macOS
  `screencapture -x screen.png`, Windows PowerShell `Add-Type -A System.Windows.Forms` plus `CopyFromScreen`.
  If it fails or hangs (a locked screen, no display), say so in one line and ask for a photo instead.
- **A video or link**: `transcribe_media` for what was said, and grab the frame they mean with
  `ffmpeg -ss <seconds> -i <video> -frames:v 1 frame.jpg`. For a page, read it.
- **Nothing**: draw from what you know. Check facts that have numbers (thrust, sizes, IDs, prices) before you put
  them on a picture: a confident wrong number on a diagram is worse than none.

## 2. Pick the form (one of three)

| Ask | Form | Template |
|---|---|---|
| How something works inside: data, a process, a system, a concept, before and after | **2D diagram** (default) | `new` |
| A physical object where shape and position matter: a jet, an engine, a joint, a room | **3D drawing** | `new --3d` |
| How to do something in order: assemble, connect, set up, fix | **Steps**: 2D with `data-step` parts, render with `--steps` | `new` |

2D is the default because it's the fastest and clearest. Use 3D only when seeing the object from an angle is the
point, and keep it to primitives (boxes, cylinders, extruded outlines): a clear low poly model beats a detailed
wrong one.

## 3. Draw it

Edit `visual.html`. Replace the title, the subtitle, the figure and the footer; keep the rest.

- **Title is the answer**, 2 to 7 words ("How an F-35B hovers"). **Subtitle**: one plain sentence on how.
  **Footer**: the one line to remember, with the key words in `<b>`.
- **One idea per picture.** If it needs more, it's two pictures or steps. Show at most about 7 labelled things.
- **2D**: the figure is an inline SVG, 936 wide and about 880 tall. Use the classes in the template (`.box`,
  `.box.soft`, `.box.warm`, `.label`, `.text`, `.note`, `.mono`, `.line`, `.arrow`, `.hit` and `.num` for numbered
  dots). Line things up on a grid, compute positions (columns at `x = 20 + 130 * i`), and write a short loop in
  your head or in Python rather than nudging by eye. Use real content: real token IDs, real port names, real code.
- **3D**: build the scene between the `the scene` markers with `part(geometry, colour)` (matte with an ink outline),
  `arrow(from, direction, length)` for forces and motion, and `label(point, "text\nsecond line", dx, dy, "accent")`
  for crisp text pinned to a point (labels move themselves apart if they overlap). Then call
  `fit([objects], new THREE.Vector3(x, y, z))` with the direction to look from: it frames everything between the
  title and the footer. Draw quantities to scale when you can and say so ("1 unit = 10,000 lbf").
- **Steps**: tag parts with `data-step="N"` (they appear from step N, earlier ones dim; add `data-keep` to stop
  that) and give each step a footer line with `data-only="N"`. The full picture (`visual.png`) shows every step
  numbered; `steps.mp4` builds it up one step at a time. 3 to 6 steps.

Look rules (from the website-design skill): one background, ink text, muted secondary text, **one accent for the
one thing to look at**, a warm second colour only when there are two things to contrast (input and output, before
and after). Real colours for real things (a green circuit board). No gradients, glows, shadows, emoji, icons on
every box or tracked out ALL CAPS labels. Hierarchy from size and position, not boxes around everything.
IBM Plex Sans and Plex Mono (mono only for real code, numbers and identifiers).

**Phone sizes**: the PNG is 1080 wide and seen about 390 points wide, so divide by 2.8. Labels 28 to 32 px, notes
at least 26 px, nothing under 24 px. Keep 72 px clear at the sides.

## 4. Look before you send (every time)

Open `visual.png` and look at it. Check, and fix what fails before anyone sees it:
1. Can you tell what it explains in 3 seconds, without the text reply?
2. Does any text overlap a line, a shape or other text? Is anything clipped or off the frame?
3. Is every label readable at phone size, and every fact right?
4. Is the accent on the one thing that matters, and only there?
5. For steps, look at two step frames too: does each one show only what that step needs?

The first render nearly always has a collision or a cramped corner. Fix it and look again (aim for two rounds,
keeping the whole thing inside a couple of minutes).

## 5. Send it

Copy `visual.png` to the outbox (and `steps.mp4` for steps: it plays on the phone). In the reply give one or two
lines that add to the picture rather than repeat it, and say what's simplified ("not to scale", "Pi 4; a Pi 5
uses a smaller connector"). From the app, also say where it's saved.

## 6. Follow ups edit the same picture

"Make it simpler", "zoom into the lift fan", "add the numbers", "what about the Pi 5", "go back to step 2": open the
same `visual.html`, change only what they asked, render, look, send. Before a big change copy the current pair to
`v1.html` and `v1.png` so "the previous one was better" is one copy away. A new question gets a new folder.

## If the renderer can't run

Chrome on its own draws the same file: `chromium --headless --hide-scrollbars --window-size=1080,1350
--virtual-time-budget=4000 --screenshot=visual.png file:///full/path/visual.html` (add
`--use-angle=swiftshader --enable-unsafe-swiftshader` for 3D). The templates load fonts and Three.js from the web;
offline, text falls back to the system font and 3D needs a local copy of three.module.min.js.

## Limits

Pictures arrive as messages; nothing is drawn live on top of their screen (that needs a desktop overlay app).
Everything is drawn from code, so photoreal objects are out: when the real look matters, label their photo instead.
