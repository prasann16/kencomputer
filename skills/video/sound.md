# Sound: clean the audio, sound effects, music

## Clean noisy audio (Voice Isolator)

Fans, traffic, echo, a café: keeps only the voice.

    <ken> --eleven isolate --in videos/x/source.mp4 --out videos/x/clean.mp4

A video in and a video out keeps the picture and swaps in the clean sound; audio in gives audio out. Up to 500 MB
and 1 hour, about $0.12 a minute. It removes music too, so clean before adding music, not after. When the source is noisy, clean it
before the cut and give the cut the clean file.

Free first try for light hiss: `ffmpeg -i in.mp4 -af "highpass=f=80,afftdn=nf=-25" -c:v copy out.mp4`. If that's
not enough, use the isolator.

## Sound effects

    <ken> --eleven sfx --text "soft whoosh, quick, for a text sliding in" --seconds 1 --out videos/x/sfx/whoosh.mp3

0.5 to 30 seconds; describe the sound, not the scene. `--loop` for an ambience bed. Make 1 to 3 per reel at the
moments graphics land, not on every cut. Mix under the voice with the effect at about -12 dB, at its moment (3 s here):
`ffmpeg -i reel.mp4 -i whoosh.mp3 -filter_complex "[1:a]volume=-12dB,adelay=3000|3000[s];[0:a][s]amix=inputs=2:duration=first:normalize=0" -c:v copy out.mp4`

## Background music

    <ken> --eleven music --prompt "warm lofi beat, 90 bpm, no vocals, calm, for a talking video" --seconds 60 --instrumental --out videos/x/music.mp3

Needs a paid plan. About $0.15 a minute, cleared for commercial use on paid plans. Always `--instrumental` under
speech. Match the reel's length; mix at about -20 dB under the voice with a fade out
(`[1:a]volume=0.1,afade=t=out:st=<len-2>:d=2[m];[0:a][m]amix=inputs=2:duration=first:normalize=0`). Keep
`normalize=0`: without it amix halves everything while the music plays, and the voice drops by 6 dB.

Trending songs: never bake a copyrighted track into the export. Tell them to pick the sound in the app.
