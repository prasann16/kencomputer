# Voices: clone theirs, voiceovers, fix a word, change a voice, design one

## Clone their voice (once)

Only the voice of the person you're chatting with. `eleven.py check` shows `can_clone`. On Ken's account it's
included in paid plans (not the free trial; say so plainly if it's false); on their own key it needs a paid
ElevenLabs plan (Starter and up).

1. **Consent, in chat.** Ask once, in your own words: "this is your own voice and you're OK with me cloning it?"
   A yes is enough. If their ask already says it plainly ("clone my voice from this video of me"), that counts:
   don't ask again. Keep their words for step 3.
   **One hard rule:** never clone a voice that belongs to someone else, when they say so or you can tell (a public
   figure, a podcast guest, a clip of another person, "my friend said it's fine", "my client"), unless that person
   is the one saying yes to you. Refuse in one friendly line and offer a stock or designed voice instead
   ("I can only clone your own voice, but I can find or design one that sounds close"). That no is final.
2. **Samples.** A voice note or recording of them talking: clone from it as it is (step 3; the helper sends up to
   2 minutes). If `eleven.py estimate clone <file>` says `noisy`, suggest once a quiet re-record; clone anyway if
   they'd rather not. Nothing sent: ask for a 1 to 2 minute voice note; only if they don't send one, find their voice
   in what exists (the videos they sent, their workspace, their own media folders):

       <ken> --eleven clone-from [file or folder ...] --out-dir voice/

   With no files named it looks in what they sent Ken, Ken's workspace, then Videos, Movies, Music, Desktop,
   Downloads. In a folder it only takes files named like their own recordings (screen recordings, voice notes,
   phone camera names like VID_/IMG_/PXL_, date-stamped files); a file named like a download (`karpathy-talk.mp4`)
   is someone else's, unless you name that file on its own or they say everything in the folder is them
   (`--any-name`). It transcribes up to 5 files (the first 10 minutes of each) with speaker labels, keeps only the
   main speaker's runs of talking (other voices, music, laughter and pauses over 0.7 s are cut out), skips
   conversations where no voice clearly leads, and joins up to 2 minutes into `voice/sample.mp3`. Voice isolation
   (`--clean always`) only when they ask, or the clone sounds bad. `voice/clone-from.json` lists every file looked at, every segment
   used and why the rest were skipped. Another file only joins the first if it's a solo recording, so two people
   never end up in one voice.
   - Its JSON `used` names the file(s): say it in the same reply as the test line, one line ("made it from your
     Aug 21 screen recording, 1:40 of you talking"). If a used file has `"others": true` (other voices were cut
     out), or you have any doubt it's them, name the file and ask "that's you talking?" before step 3.
   - `"short": true` (30 to 60 s found) still clones, but say a 1 minute voice note would sound better.
   - It fails when there's under 30 s of them alone: ask for a 1 minute voice note, just them talking normally in a
     quiet room, then `clone-from <the note>`.
3. Make it, with their yes in their words:

       <ken> --eleven clone --name "<their name>" --sample voice/sample.mp3 --said "<their yes, as they wrote it>" --own-voice

   `--own-voice` is you confirming steps 1 and 2. The helper adds who, when, what they said and which files to the
   consent record (`voice/consent.json`, next to the samples) and refuses without a yes. A voice note or written
   consent they sent works too: `--consent voice/consent.ogg` instead of `--said`.
4. Save the returned `voice_id` in your memory (their voice note: name, voice_id, date, the consent record). Keep it
   for their next videos: it's theirs. Test it with a 10 second TTS line and send it: "this is you, sound right?"

Cloning again replaces their old voice (JSON `replaced`): say "Updated your voice." Delete only when they clearly ask
in chat: `voice-delete <id>` says what to ask; on their yes add `--confirmed`, say "Done, your voice is deleted." Keep
`voice/sample.mp3`.

## Voiceover from a script

    <ken> --eleven tts --voice <voice_id> --text-file videos/x/script.txt --out videos/x/vo.mp3

- Voice: their clone, a voice they picked (`eleven.py voices --search calm`), or a designed one.
- `--model eleven_multilingual_v2` (default): always for their cloned voice, in any language. Never `eleven_v3` on
  a clone (it doesn't sound like them); v3 only for a stock voice. `eleven_flash_v2_5` is half price, for drafts.
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

5 minutes per file at most (split longer ones). A video in and a video out keeps the picture.

## Design a new voice

When they want a narrator or character voice that isn't anyone real:

    <ken> --eleven design --describe "warm, calm Indian woman in her 30s, slow, documentary narrator" --out-dir voice/design

Send them the previews (2 to 3 short clips). Save the one they like:

    <ken> --eleven design-save --id <generated_voice_id> --name "Narrator" --describe "..."

and use the returned `voice_id` like any other. Note it in memory.
