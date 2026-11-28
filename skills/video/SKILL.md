---
name: video
description: Edit any video into something they'd post, and everything around it. Talking videos into reels, clips with no talking (outfits, B-roll, trips) into edits with text, a long video or podcast into shorts, captions and subtitles in any language, hooks, graphics and explainer animations; plus voice and sound with ElevenLabs (voiceover, their own cloned voice, fixing a wrong word, dubbing into another language, a script in their style, cleaning noisy audio, music and sound effects). Use whenever they send a video or ask for a reel, an edit, captions, a dub, a voiceover or sound for a video, or changes to one made earlier.
---

# Video

They send footage; you hand back something they'd post untouched. There's no house style and no fixed recipe:
watch what they sent, work out what they're trying to say or show, and decide the edit for this video. Work in
`videos/<short-name>/` in your workspace (that folder is the project, so changes later pick up where you left off)
and never change their original file. `<ken>` below is the exact command in your instructions.

## Your tools

| For | Use |
|---|---|
| Cutting: trims, joins, crops to 9:16, speed, converting | ffmpeg. Only cutting: never burn text or captions in with it (no `subtitles` or `drawtext` filter) |
| Their words, timed | `transcribe_media` (free); ElevenLabs Scribe for Hindi, Hinglish, accents, noisy audio (`captions.md`) |
| Cutting a talking video at the voice's real edges | `<ken> --video-cut <folder>` (`talking.md`) |
| Anything drawn on screen: captions, the hook, titles, text, graphics, explainers | Remotion, through the kit: `props.json`, the toolbox, your own components (`graphics.md`) |
| Looking at frames | `<ken> --video-stills <folder> <seconds...>` |
| A link that plays the edit, nothing rendered | `<ken> --video-preview <folder>` |
| The finished video | `<ken> --video-render <folder>` |
| Voice, dubbing, music, sound effects, cleaning audio | `<ken> --eleven <command>` (ElevenLabs, see Voice and sound below) |

## How to edit a video

1. **Watch it all.** Copy it into the folder as `source.<ext>` (several clips: keep each one's name). Transcribe
   anything with talking and read the whole transcript, then look at the footage: a contact sheet of frames every few
   seconds (`ffmpeg -v error -y -i source.<ext> -vf "fps=1/3,scale=270:-1,tile=6x6" -frames:v 1 checks/source.jpg`)
   and closer frames of anything worth showing. Know who's there, what's in frame, what each part says or shows.
2. **Decide the edit and write `plan.md`**: what it says in one sentence and how long it'll be, the opening (the line
   or shot it starts on, and any hook text), what stays and what goes in order, every piece of text and every graphic
   with the moment it comes in, and the captions (their exact words) if there's talking.
3. **Tell them the plan** in a few short lines, the main moves only, and ask if you should go ahead. **Then stop and
   wait.** Only words that say to skip it skip it ("just do it", "no need to check"), in this message or in your
   memory. A change to something already made needs no plan.
4. **On their yes, the whole edit is a background job**: the folder, what they asked, `plan.md`. Tell them in one line
   it's running and roughly how long. Never cut, design or render in the chat itself.
5. **The job** cuts (ffmpeg, or `--video-cut` for talking), designs in Remotion, checks stills, renders, watches the
   result, then sends the finished video. Nothing is named or sent while it's still being made.

What kind of video it is decides the cut:

- Someone talking to camera: `talking.md`.
- Clips with no talking, or a long video into shorts: `clips.md`.
- Design, text and captions for any of them: `graphics.md`.
- The opening, or why one flopped: `hooks.md`.

## Rules for every video

- **Their words are exact.** Captions never shorten, tidy or paraphrase. Fix what transcription got wrong (names,
  product names, numbers, web addresses) against the audio first.
- **Their style.** Read your memory for their video style (`memory/video-style.md`). None yet: decide the look for this
  video yourself, don't ask. Save only what they asked for (a reel to match, their colours or fonts, rules like "no
  emojis"), right away, so they never say it twice. Never save a look you chose as their style.
- **Safe zones**: the apps put buttons over the top 250 px, the bottom 480 px and the right 140 px. Keep text and
  graphics out of them. Vertical videos are 1080x1920.
- **One render or stills at a time**, never in parallel (each opens Chrome and uses about a gigabyte). The first time
  sets up Remotion (a few minutes): say so in one line.
- **HDR phone footage** is converted to normal colours by the kit: never convert colours yourself.

## Check, render, send

- **Stills before rendering**: all the times in one command (`<ken> --video-stills <folder> 1.5 10.5 24`), then look at
  `checks/sheet.jpg` (safe zones tinted red). Look for cut-off text, text in a red zone, a graphic over a face,
  captions colliding with a graphic. Would a good editor be proud of this frame?
- **Render** with `<ken> --video-render <folder>` in the background job; `--plan` first says how much it would draw,
  and a fix later redraws only the seconds it changes. Then one look at the result: its length with ffprobe and a strip
  of frames (`ffmpeg -v error -y -i reel.mp4 -vf "fps=1/6,scale=216:-1,tile=8x1" -frames:v 1 checks/reel.jpg`).
  Don't send something you wouldn't post.
- **Send** the finished video with one confident line on what you did and one question, never a file path or how you
  checked. Videos stay 7 days: say so.
- **Changes later** ("bigger captions", "drop that bit"): change only that in `props.json` (or cut again with new
  ranges), check stills of what changed, then send the preview link (`--video-preview` reads `props.json` when opened,
  so the same link shows each change: "same link, reload it") and render again in the background once they say it's
  right. Note every correction in their style note.

## Voice and sound (ElevenLabs)

`<ken> --eleven check` once per chat, before the first ElevenLabs call, says whose account it runs on. In the files below, `eleven.py <command>`
means `<ken> --eleven <command>`. Read only the file the ask needs:

| They say something like | Read |
|---|---|
| "dub this in Hindi", "same video in Spanish" | `dub.md` |
| "a script in my style", "say this in my voice in Hindi and Spanish" | `script-in-my-style.md` |
| "clone my voice", "voiceover for this", "I said the wrong price, fix it", "a different voice" | `voice.md` |
| "clean the audio", "remove the fan noise", "background music", "a whoosh" | `sound.md` |
| "captions", "subtitles", "SRT", "Hinglish captions" | `captions.md` |

Several at once run in this order: clean, cut, captions, dub (dub the finished cut, so they pay for minutes that stay).

- **Whose account.** Their own `ELEVENLABS_API_KEY` in Brain always wins (`"account": "own"`: their money). Otherwise
  `"account": "ken"` runs on Ken's account and comes out of their Ken AI credits: they need no ElevenLabs account or
  key, never ask for one. If it isn't on for them, the helper says so in one line: pass that on.
- **Say what it costs.** Before anything costly (a dub, long music), `<ken> --eleven estimate <feature> <file>` and say
  it in one line, then go (on their own key, over about $0.50 or any dub: wait for a yes). Prices: `prices.md`.
- **Their own voice only.** Clone only the person you're talking to, after one yes in chat (`voice.md`). Never a
  celebrity, friend or client unless that person says yes to you themselves.
- **Label synthetic media.** When they're about to post a dubbed, cloned or changed voice, remind them once to switch
  on the platform's AI label.
- **Only their content**: videos they made or have rights to.
- **Fail once, plainly.** If ElevenLabs says no (credits, down, too big), tell them in one line with the fix; never
  retry in a loop. Listen to what you made before sending: ffprobe the length, `transcribe_media` a voice to check names.
