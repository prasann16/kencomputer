# Dub a video into another language

ElevenLabs Dubbing translates the speech and re-voices it in each speaker's own voice (it clones them for the
dub only), keeping music and background sound. They get back the same video in the new language.

## First, is it worth paying for?

Instagram/Facebook Reels translate into 9 languages for free (Hindi, Bengali, Tamil, Telugu, Kannada, Marathi,
English, Spanish, Portuguese) and YouTube auto-dubs into 27. If they only post there in one of those, say so in
one line: it may be free in the app. Paying makes sense for LinkedIn, X, WhatsApp, ads, their own site, languages
Meta skips (Gujarati, Malayalam, Punjabi, Odia and more), or when they want control over the translated script.

## Steps

1. The video is in `videos/<short-name>/`. If they also want it cut or cleaned, do that first (dub last).
2. `eleven.py estimate dub <file>`. On Ken's account: say in one line how many of their dub minutes it uses (each
   language uses the reel's length again) and go. On their own key: tell them the cost per language (a 60 s reel
   is about $0.50 a language) and wait for the yes (more than one language: one total).
3. Run it as a background job, one command per language:

       <ken> --eleven dub --in videos/x/reel.mp4 --to hi --out videos/x/reel.hi.mp4

   `--to` and `--from` are language codes (`hi`, `ta`, `te`, `bn`, `mr`, `gu`, `ml`, `pa`, `es`, `pt`, `fr`, `de`,
   `ja`, `ar`...). Leave `--from` out to detect it. `--speakers 1` when it's just them talking (faster, cleaner).
   `--url` instead of `--in` dubs a YouTube or TikTok link of their own video (add `--seconds` if you know its length).
   On Ken's account only the audio goes up, and the dubbed audio comes back over their original picture.
   It waits for the dub (a 1 minute reel takes a few minutes) and writes the dubbed video plus translated
   subtitles (`reel.hi.hi.srt`). If it says "still working", run `dub-get <job> --to hi --out ...` later.
4. Check it: `transcribe_media` on the dub, read the translation for wrong names, brand names and slang, and look
   at the length. Send it with one line on what's in it and the AI label reminder.

## Choosing the engine

- Default (no flag): Dubbing v1, about **$0.50 a minute**, no watermark, returns the finished video. Good for reels.
- `--v2`: Dubbing v2, about **$2.20 a minute**, newer voices and translation, returns lossless audio and subtitles
  that the helper puts back over the original picture. Use when they ask for the best quality, or when v1 says it's
  no longer available (ElevenLabs marked it deprecated in 2026).
- On Ken's account the prices above are never said; v2 has its own, smaller allowance (`dub_v2` in `check`), so
  use it only when they ask for the best quality.
- On their own key, a free ElevenLabs plan watermarks dubs. Say so if `check` shows tier `free`.

## When they want it "in my voice" in another language, with their own script

The dub already speaks in their voice. If they want to control the words (fix slang, keep English tech words in a
Hindi dub), write the translated script with them, then make it with their cloned voice and TTS
(`flows/voice.md`, "Voiceover from a script"), and lay it over the cut with ffmpeg. Slower, but every word is theirs.

## Lip sync

ElevenLabs doesn't move the lips. If they need it, it's a different service (HeyGen, sync.so, about $2/min) and a
key they'd add; say so rather than pretending.

Limits: 3 GB per file through the API, up to 32 speakers, 3 dubs at once on a self-serve plan.
