# Clips: a long video into shorts

For a podcast, stream, class or long YouTube video they made. Not someone else's: that's a copyright claim.

1. Copy it into `videos/<short-name>/` as `source.<ext>`. Transcribe it with `transcribe_media` (free).
2. Read the whole transcript and pick 5 to 10 moments that stand on their own: 20 to 60 seconds, a strong first
   line (see `flows/hooks.md`), one complete thought, no "like I said earlier". List them for them: the first line
   quoted, the length, why it works. Wait for their picks.
3. Each picked clip becomes its own folder `videos/<short-name>-<n>/` with that stretch as `source.<ext>`:

       ffmpeg -v error -ss <start> -to <end> -i source.mp4 -c copy videos/<short-name>-1/source.mp4

   (re-encode with `-c:v libx264 -crf 18 -c:a aac` if the cut must be frame exact.)
4. Then each clip goes through the video skill (cut, captions, hook, render). That's slow: one background job for
   all of them, done one at a time, never in parallel.

## Vertical crop without the full edit

When they only want it vertical and captioned:

- Speaker in the middle: `ffmpeg -i in.mp4 -vf "crop=ih*9/16:ih,scale=1080:1920" -c:a copy out.mp4`
- Speaker off centre: take a frame (`ffmpeg -ss 5 -i in.mp4 -frames:v 1 f.jpg`), look where the face is, and set
  the crop's x: `crop=ih*9/16:ih:<x>:0`. Two people: pick per clip who's talking, or stack them (top/bottom).
- Then captions (`flows/captions.md`, burned in).

Big source files: say where the clips are and that videos stay 7 days.
