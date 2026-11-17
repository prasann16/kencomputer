# Voices: clone theirs, voiceovers, fix a word, change a voice, design one

## Clone their voice (once)

Only the voice of the person you're chatting with. Instant Voice Clone needs a paid ElevenLabs plan (Starter, $6 a
month, and up): `eleven.py check` shows `can_clone`.

1. **Consent, recorded.** Ask them to send a voice note saying, in their words: "I'm <name>, this is my own voice,
   and I agree to Ken making a copy of it for my own videos." Save it as `voice/consent.<ext>` in your workspace.
   Listen to it with `transcribe_media`: the name and the agreement must be there, in the same voice as the
   samples. If it doesn't sound like the same person, or they're asking for someone else's voice, stop: no clone.
2. **Samples.** 1 to 2 minutes of them talking naturally, alone, no music, in the language they'll use most (best:
   a clean part of their videos, or a voice note read in a quiet room). Run `eleven.py isolate` on a noisy one
   first. Save in `voice/`.
3. Make it:

       <ken> --eleven clone --name "<their name>" --sample voice/s1.mp3 --consent voice/consent.ogg --own-voice

   `--own-voice` is you confirming steps 1 and 2. The helper refuses without the consent file.
4. Save the returned `voice_id` in your memory (their voice note: name, voice_id, date, consent file). Test it with
   a 10 second TTS line and send it: "this is you, sound right?"

Delete it if they ask (in their ElevenLabs account, Voices) and remove it from memory.

## Voiceover from a script

    <ken> --eleven tts --voice <voice_id> --text-file videos/x/script.txt --out videos/x/vo.mp3

- Voice: their clone, a voice they picked (`eleven.py voices --search calm`), or a designed one.
- `--model eleven_multilingual_v2` (default) for quality in 29+ languages; `eleven_flash_v2_5` is half price and
  faster, fine for drafts; `eleven_v3` for the most expressive read (5,000 characters per call).
- Hindi, Hinglish and names: write numbers and names how they're said ("twenty four seven", not "24/7"). Listen and
  fix before sending.
- Costs about $0.08 per 1,000 characters (about a minute of speech).
- Put it under the video: `ffmpeg -i video.mp4 -i vo.mp3 -map 0:v -map 1:a -c:v copy -shortest out.mp4`, or mix it
  with the original sound (`amix`) when it's a voiceover on top.

## Fix a wrong word without reshooting

They said the wrong price or name. Only with their own cloned voice.

1. Find the word's time in `source.words.json` (from `transcribe_media`), and take the whole phrase around it,
   pause to pause, so the new line sounds natural.
2. TTS the corrected phrase with their voice. Use `--speed` to match their pace (0.9 to 1.1).
3. Cut it in with ffmpeg: audio before + new phrase + audio after, a 20 ms crossfade at each join
   (`acrossfade=d=0.02`), the picture untouched. If the new phrase is longer, slow the picture a little over that
   stretch or cover it with a graphic (video skill). Send both versions' seconds so they can compare.

## Change a voice (speech to speech)

Keeps their delivery, timing and emotion, swaps the voice. For a character, an anonymous voice, or their own
cleaner voice over a rough take. Not someone else's real voice.

    <ken> --eleven changer --voice <voice_id> --in take.mp3 --out new.mp3 [--denoise]

5 minutes per file at most (split longer ones). About $0.12 a minute. A video in and a video out keeps the picture.

## Design a new voice

When they want a narrator or character voice that isn't anyone real:

    <ken> --eleven design --describe "warm, calm Indian woman in her 30s, slow, documentary narrator" --out-dir voice/design

Send them the previews (2 to 3 short clips). Save the one they like:

    <ken> --eleven design-save --id <generated_voice_id> --name "Narrator" --describe "..."

and use the returned `voice_id` like any other. Note it in memory.
