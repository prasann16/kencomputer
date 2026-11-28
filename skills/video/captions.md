# Captions and subtitles

Two engines. Pick by the language, not by habit.

- **Free, on this computer: `transcribe_media`.** Good for clear English (and most single-language speech). It
  writes the transcript, `source.words.json` and subtitles. Use it first when there's no ElevenLabs key.
- **ElevenLabs Scribe** (about $0.20 an hour of audio, so a reel costs under a cent): better for Hindi and other
  Indian languages, Hinglish, accents, several speakers and noisy audio.

      <ken> --eleven captions --in videos/x/reel.mp4 --out videos/x/reel.srt [--lang hi] [--speakers]

  It writes `reel.srt` (short lines for vertical video, a new line at each sentence) and `reel.words.json`
  (every word with its time). `--max-chars 42` for wider landscape lines. Files up to 5 GB, 15 hours.

## What they get

- **"Add captions" on a reel:** animated word-by-word captions in Remotion (`design.md`). Give it the words (fix names and
  brand names first). This is the default for Reels/Shorts.
- **An SRT to upload** (YouTube, LinkedIn, X): send the `.srt`. Uploaded captions stay searchable and can be
  switched off by viewers.
- **Captions on the video itself** are always Remotion (`design.md`), even for plain ones: never burn an SRT
  in with ffmpeg. Give it the words from `--eleven captions` or `transcribe_media`.
- **Another language's subtitles:** translate the SRT yourself line by line, keeping every timestamp, or take the
  `.srt` a dub writes.

## Hinglish

Most Hinglish creators want Roman script ("kya scene hai"), not Devanagari. Ask once which they want and save it
in their video style note. Scribe may write Hindi words in Devanagari: transliterate those lines to Roman yourself,
keeping English words as they are. Read every line against the audio before sending; names and slang are where
both engines go wrong.
