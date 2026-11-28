# A new script in their style, read in their voice (and other languages)

They want a new video on a topic, sounding the way they usually talk: you write the script from their own past
content, show it, then make the audio in their cloned voice (or a stock voice), in other languages if they ask, and
put it over their footage with captions. No lip sync: it's a voiceover over footage, b-roll or a screen recording.

## 1. Read their style (free, no ElevenLabs)

Find their own words, best first:

- **Their past videos' transcripts** in your workspace: `videos/*/source.words.json`, `*.srt`, transcripts. What
  they said out loud is the best guide for a script that will be said out loud.
- **Posts or captions they point to**: a link (read it; X or Instagram in the browser, your own tab, closed after),
  or a few they paste.
- Your memory's notes on how they write (their video style note, posts you've drafted for them that they approved).

Aim for 3 or more pieces, a few hundred words of their speech. Only their own content: a creator they admire is a
reference for the format, never a voice to copy. If you find nothing, ask once for 2 or 3 of their posts or a past
video; don't invent a style.

## 2. Write down the style

`videos/<short-name>/style.md`, each point with a short quote from them as the evidence:

- **Hook**: how their first line usually goes (a question, the result first, "so I built...").
- **Sentences**: about how many words, and how they join them ("and", "so", "right?").
- **Their words**: the words and fillers they really use ("basically", "simply put", "lol", "tbh"), and words they
  never use.
- **Tone**: casual or formal, jokes, how they talk about themselves.
- **Pace**: words a minute from a `words.json` (words ÷ speaking seconds), so you know how long a script will run.
- **Shape**: how their videos usually go (demo first, then how it works, then "yeah so that's it").

Add the lasting part to their video style note in memory, so the next script starts from it.

## 3. Write the script

- The length they asked for, from their pace: a 30 s reel at 150 words a minute is about 75 words. Ask nothing
  about length if they didn't say: 30 to 45 seconds.
- Their hook pattern in the first line, their words, their shape. Read it aloud in your head in their voice.
- **Only what's true.** Facts from them, their memory notes or what they sent: never a made up number, customer or
  result. If the topic needs a fact you don't have, leave a gap and ask.
- Written for the ear: numbers and names as they're said ("twenty four seven", "Sarvam"), no lists, no symbols.

## 4. Show it first

Send the script in a code block with one line on the style moves you used ("opens with the result like your voice
engine video, your 'basically'") and which voice you'll use. If they asked for other languages, send those scripts
too, each with a line on what you kept in English. **Wait for their OK.** Nothing is generated before it, and their
edits go in word for word.

## 5. Make the audio

- **Voice**: their clone if they have one (memory). No clone yet and they want their voice: `voice.md` (one
  consent question in chat, then clone from their past videos). Or a stock voice they pick (`eleven.py voices`).
- `<ken> --eleven tts --voice <id> --text-file videos/x/script.en.txt --out videos/x/vo.en.mp3`
  (`eleven_multilingual_v2`, the default, keeps their voice in other languages too).
- Listen: `transcribe_media` on the file, read it back against the script, check the length with ffprobe. A wrong
  name or a word read oddly gets respelled in the script and made again.

## 6. Other languages

They said "in Hindi and Spanish too": translate the approved script yourself, as a native speaker would say it, not
word for word. Keep the words people really say in English (in Hindi: "voice agent", "API", "startup"); Hindi in
Devanagari reads best. Same voice, same command, one file per language (`vo.hi.mp3`, `vo.es.mp3`). Check each with
`transcribe_media` and its `language`.

This is cheaper than a dub and every word is theirs to approve. To translate a finished video they already made,
use `dub.md` instead.

## 7. Over their footage, with captions

Their footage, b-roll or screen recording goes under the voice:

    <ken> --eleven over --video videos/x/broll.mp4 [--video videos/x/broll2.mp4] --audio videos/x/vo.en.mp3 --out videos/x/source.mp4

The picture loops or runs clip after clip for as long as the voice, their old sound goes (`--bed 0.1` keeps one
clip's sound quietly under it), and nothing else changes. `--start 40` starts a single clip 40 s in. Then it's a
normal talking video for the **video** skill: `transcribe_media` on `source.mp4`, fix `source.words.json` to the
script's exact words, cut all of it, render. One folder per language (`videos/x-hi/`), so each reel gets captions in
its own language.

## 8. Send, then post

Send the audio and the reel. Posting waits for their yes on that exact file, and gets the AI label reminder
(SKILL.md). Keep the voice for next time: it's theirs, in memory.
