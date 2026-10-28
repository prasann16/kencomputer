---
name: website-design
description: Use for ANY website, landing page, waitlist page, portfolio, product page, UI screen or HTML/CSS you build or restyle for them or their ideas. Stops generic "AI slop" looks (purple gradients, Inter + rounded cards, emoji bullets, hype copy) and makes you review your own screenshots before anyone sees the page.
---

# Website design: crafted, not slop

Models default to the statistical middle of the web ("distributional convergence"), so an unguided page looks like every other vibe coded page. Your job is to direct, then check your own work with your eyes. Read this before writing the first line of HTML.

## 1. Decide before you code (write these 6 lines at the top of your notes)
1. Who it is for and the ONE job of the page (sign up, book a call, try the demo).
2. ONE visual direction taken from the product's own world, not "modern SaaS". Examples: a terminal tool looks like a well set man page; a meditation app looks like a printed pocket book; a voice AI product leads with a waveform and a real call transcript; a portfolio reads like a short editorial CV.
3. ONE memorable element (a live demo, a real transcript, a big real screenshot, a type treatment). Everything else stays quiet.
4. Fonts: 1 or 2 families, named (see section 2).
5. Palette: 4 to 6 named hex values (see section 3).
6. The real content you have (section 4). If you have none, stop and get it.

## 2. Typography first
- Type does most of the design. Scale: 14, 16, 18, 22, 28, 36, 48, 64 px. Body 17 to 18 px, line height 1.5 to 1.65, lines under 72 characters. Headings tight (line height 1.1, slight negative tracking).
- Do NOT reach for: Inter, Roboto, Arial, Poppins, Montserrat, Space Grotesk, Instrument Serif, Geist, or an italic serif accent word inside a sans headline. These are the current AI tells.
- Pairings that work (Google Fonts or system, pick by direction):
  - Editorial / personal: Newsreader or Source Serif 4 headings + Public Sans body. Or a zero download serif stack: `"Iowan Old Style", Charter, "Bitstream Charter", Georgia, serif`.
  - Technical / dev tool: IBM Plex Sans + IBM Plex Mono (mono only for real code, not for labels).
  - Warm product: Literata + Work Sans. Bold consumer: Archivo (wide weights) + Source Serif 4.
  - Fastest: the system stack `system-ui, -apple-system, "Segoe UI", sans-serif` set with real care beats a trendy webfont set badly.
- Max 2 weights per family. Self host or `display=swap`, subset, preload the one heading font.

## 3. Colour and surfaces
- Mostly one background, one text colour, one muted text colour, one rule colour, ONE accent used on maybe 5 places (links, the main button). Check contrast 4.5:1 for body text.
- Banned clusters: indigo/violet/purple anything, blue to purple gradients, cream + terracotta, near black + acid green or vermilion, permanent dark mode with mid grey body text.
- No gradient washes, glows, coloured box shadows, glassmorphism, 3D blobs, noise overlays, or decorative grids. One subtle shadow is allowed if something truly floats (a menu).
- Hierarchy comes from size, weight, spacing and position, not from boxes. Prefer rules and whitespace over cards. If you use cards, vary them by importance, never 6 identical rounded ones.

## 4. Real content, never filler
- Use real things: product screenshots at real size, a 20 to 40 second demo video (muted, with controls, poster frame), real numbers, real links, real names who agreed.
- For a personal or portfolio page, use their own work: jobs, projects, links and numbers from their memory files, CV, existing site or repos. Every fact must trace to something they wrote or shipped. If a number isn't on record, leave it out, never estimate.
- Never: fake testimonials, fake logo walls, "Trusted by 10,000+ teams", made up stats, lorem or "Feature one" placeholders, stock photos, AI illustrations of people.
- If the product has nothing to show yet, show the honest thing: what it does in one sentence, one sketch or a short screen recording, and a waitlist.

## 5. Copy rules
- Headline says what it is and who it is for, concretely. "Turn plain English into the right shell command, offline" not "Revolutionize your workflow". Harry Dry test: can you picture it, can it be proven false, could a competitor not say it?
- Subhead: one or two plain sentences on how. Buttons say the action ("Join the waitlist", "Watch the 30 second demo"), no arrows.
- Banned words: revolutionize, supercharge, unleash, unlock, elevate, seamless, effortless, robust, cutting edge, next gen, game changer, leverage, delve, empower, AI powered (as a headline), "built for the future".
- In their own voice: reuse how they actually write (their messages, posts, any style notes in memory). Short sentences, first person, lead with what they built and link to it. No em dashes, no emojis, no "A · B · C" strings.
- Fewer sections. A good landing page is often: hero, the demo/visual, 3 to 5 concrete points tied to the headline, the main objection answered, CTA, small footer. No FAQ padding, no "How it works 01 02 03" unless it truly is steps.

## 6. Layout and spacing
- Spacing scale only: 4, 8, 12, 16, 24, 32, 48, 64, 96, 128 px. More space between sections than inside them.
- Left aligned text by default. Center only short single blocks. An asymmetric grid (for example 5/7 columns) usually beats a centered stack.
- Banned: badge pill above the H1, tracked out ALL CAPS eyebrow labels, coloured top or left borders on cards, stat banner rows ("5+ projects, 2+ clients"), icon on top of every feature card, emoji as icons or bullets, the big number hero.
- Mobile first: design at 390 px, then widen. Tap targets at least 44 px, inputs 16 px text, no horizontal scroll.

## 7. Motion only with purpose
- Default is no animation. Allowed: one orchestrated moment (the demo starting, one reveal), and feedback on user actions (button pressed, form sent).
- Under 300 ms, ease out, animate only transform and opacity, never `transition: all`. Respect `prefers-reduced-motion`. No fade up on every section, no hover bounce on every card, no parallax, no autoplaying carousels.

## 8. Accessibility and performance basics
- Semantic HTML (header, main, section, h1 then h2), one h1, alt text, visible `:focus-visible`, labels on every input, `lang` set, real `<a>` for links.
- Static HTML + one CSS file, no framework unless needed. Under 200 KB total on first load excluding the demo video. Images with width and height, `loading="lazy"` below the fold, WebP or AVIF. Video uses `preload="none"` and a poster.
- Title and meta description that say what it is. An Open Graph image (a real screenshot works).

## 9. Waitlist / email capture
- One field (email, `type="email"`, `autocomplete="email"`), one button, one line saying what they get and how often. Show the confirmation in place, no page reload, with `aria-live="polite"`.
- Default backend (free, no server): a plain `<form method="POST">` to Formspree (`https://formspree.io/f/<id>`, free tier) or a Buttondown newsletter form if it is meant to become a newsletter. If their memory records a preferred form tool, use that. Creating the account needs their OK.
- No "Join 2,000 others" unless that number is real.

## 10. Mandatory self review (at least 2 rounds, before showing anyone)
Serve the folder (`python -m http.server 8765 --bind 127.0.0.1`) and screenshot both widths, one chromium at a time:
```
chromium --headless=new --disable-gpu --hide-scrollbars --window-size=1440,2200 --screenshot=/tmp/shots/desk.png http://127.0.0.1:8765/
chromium --headless=new --disable-gpu --hide-scrollbars --window-size=390,2600 --screenshot=/tmp/shots/mob.png http://127.0.0.1:8765/
```
No `chromium`? Use Chrome's binary (macOS: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"`, same flags) or the browser tool's screenshot at both sizes. Open each PNG with Read and actually look. Then answer in writing:
1. In 3 seconds, can I tell what this is, for whom, and what to do next?
2. Does it look like it belongs to THIS product, or could the logo be swapped onto any startup?
3. Count anti slop hits from sections 2 to 7. Target zero.
4. Is every fact real and traceable? Any placeholder left?
5. Type: clear scale, comfortable line length, no widows in the headline? Spacing on the scale?
6. Mobile: nothing cramped, clipped or overflowing, button reachable?
Fix everything you found, re screenshot, review again. Stop the server when done. Only then share, and send the final screenshots with it.

## Sources
Anthropic frontend-design skill (github.com/anthropics/skills, skills/frontend-design); Vercel Web Interface Guidelines (github.com/vercel-labs/web-interface-guidelines); Adrian Krebs, scoring Show HN pages for AI design patterns (adriankrebs.ch/blog/design-slop); prg.sh "Why your AI keeps building the same purple gradient website"; Julian Shapiro landing page guide (julian.com/guide/growth/landing-pages); Harry Dry (marketingexamples.com); Emil Kowalski (emilkowal.ski/ui/great-animations); Rauno Freiberg (rauno.me/craft/interaction-design); Refactoring UI (refactoringui.com). Crafted examples to study: rauno.me, paco.me, emilkowal.ski, linear.app.
