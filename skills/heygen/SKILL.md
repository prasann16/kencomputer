---
name: heygen
description: Make a presenter video of the creator's OWN HeyGen avatar saying a script you write in their style, vertical 9:16, ready for the video skill to add captions and b-roll. Uses HeyGen (their own key whenever they add one, otherwise Ken's own account on hosted Ken). Use when they ask for an avatar video, an AI presenter, "my avatar", "my digital twin", "a video of me saying this without filming", "a talking head from this script", or mention HeyGen. Only their own avatar and voice, never someone else's face.
---

# Their avatar says the script

A creator who doesn't want to film today still posts: you write the script the way they talk, their own HeyGen
avatar says it, and the **video** skill finishes it with captions and b-roll. Work in `videos/<short-name>/` in your
workspace, the same folder the video skill uses.

The helper is `<ken> --heygen <command> ...`, where `<ken>` is the exact command in your instructions (the same one
the video skill uses). Every command prints one JSON line. Below, `heygen.py <command>` is short for
`<ken> --heygen <command>`; `<ken> --heygen -h` lists them all.

## Rules

- **Their own avatar only.** The face and voice of the person you're talking to, after one yes in chat ("this is
  your own HeyGen avatar and voice, and you want a video made with it?"). Pass their words as `--said`: the helper
  refuses without it, and refuses any avatar or voice that isn't their own on the account. Never a celebrity, a
  friend, a client, a boss or anyone "who said it's fine", and never a stock presenter passed off as them: refuse
  in one friendly line. The same rule as voice clones.
- **Whose account.** Run `heygen.py check` once per chat before anything else. Their own `HEYGEN_API_KEY` in Brain
  always wins, hosted Ken or not: `"account": "own"`, their HeyGen credits. Otherwise `"account": "ken"` means
  hosted Ken's own HeyGen account, included in their plan, with a monthly allowance of avatar video minutes.
- **On their own key.** It reads `HEYGEN_API_KEY` from the environment: never print it, echo it, write it to a
  file or put it in a command line. It's their money: before every video, `heygen.py estimate`, tell them the
  length and their balance in one line, and wait for a yes. With no key and no host, the helper says so: tell them
  in one line to add it in the Brain tab under Accounts & passwords, named `HEYGEN_API_KEY` (app.heygen.com →
  Settings → API), and that it works from their next message. Never ask for the key in chat.
- **On Ken's account, no money talk.** No dollars, credits or plans. The only number is what's left ("that uses 1
  of your 4 avatar minutes this month"), from `estimate`. When `fits` is false or a call says the allowance is used
  up, say what's left and when it comes back, once.
- **When credits run out**, the helper says exactly what to do (top up on HeyGen, or on hosted Ken remove the key
  to go back to Ken's allowance). Pass it on as is, in one line. Never switch accounts yourself.
- **One video per yes.** A video costs credits every time it's made. Never generate twice for the same script:
  a slow or interrupted run is picked up with `heygen.py get <video_id>`, which never makes it again. A changed
  script is a new video and a new yes.
- **Label it.** An avatar video is AI generated. When they're about to post it, remind them in one line to switch
  on the platform's AI label (India's IT Rules require it since Feb 2026; Meta, YouTube and TikTok have the toggle).
- **Only what's true** in the script, and **show, then post**: nothing is posted anywhere without their yes on that
  exact file.
- **Fail once, plainly.** If HeyGen or Ken says no, tell them in one line with the fix. Never retry in a loop.

## 1. Find their avatar

    <ken> --heygen avatars

Lists their own avatar looks: `id`, `name`, `kind` (`digital_twin` is a filmed twin, `photo_avatar` is from a
photo), `orientation`, `ready`. One look: use it. Several: ask which, by name, once, and remember the id in memory
(`memory/video-style.md`) so the next video doesn't ask.

- **None yet**: they make one in the HeyGen app (Avatars → Create: a couple of minutes of them talking to the
  camera, plus HeyGen's own consent recording). You can't make it for them and shouldn't: the consent recording is
  the point. Say so in one line and offer the voiceover over b-roll instead (the video-kit skill, if it's there).
- **`"ready": false`**: still being made, or `waiting_on: consent`: they finish it in the HeyGen app.
- `portrait` looks suit 9:16 best. A `landscape` look still works; check the framing in step 5.

The avatar's own voice is used unless they ask for another of theirs (`heygen.py voices` lists their own).

## 2. Write the script in their style

The same way as the video kit's "script in my style" flow (if the video-kit skill is installed, follow its
`flows/script-in-my-style.md` steps 1 to 4 and come back). In short:

1. **Read how they talk**: transcripts of their past videos in the workspace (`videos/*/source.words.json`,
   `*.srt`), posts they point to, and your memory's notes on their style. Only their own content.
2. **`videos/<short-name>/style.md`**: hook pattern, sentence length, their words and fillers, tone, pace (words a
   minute), shape, each with a short quote from them.
3. **The script**: 30 to 45 seconds unless they said a length (at 150 words a minute, 75 to 110 words). Their hook
   pattern first, their words, only true facts. Written for the ear: numbers and names as they're said, no lists, no
   symbols, no emoji. Short sentences: an avatar reads long ones flat. 5,000 characters is the most for one video.
4. **Show it first**, in a code block, with one line on the style moves you used. Wait for their OK; their edits go
   in word for word. Save it as `videos/<short-name>/script.txt`.

## 3. Check it fits

    <ken> --heygen estimate --script-file videos/x/script.txt [--wpm 160]

`seconds` is how long it will run. On their own key say the length and their balance and wait for the yes (the
consent question and this can be one message: "45 seconds with your avatar Swap, on your HeyGen credits, go?").

## 4. Make it

    <ken> --heygen generate --avatar <look id> --script-file videos/x/script.txt \
        --said "<their yes, as they wrote it>" --own-avatar --out videos/x/avatar.mp4

Vertical 9:16 at 1080p by default (`--aspect 16:9` for YouTube or LinkedIn). It waits for HeyGen (usually a few
minutes; a minute of video can take ten) and downloads the mp4, so run it as a **background job** with the exact
command and the folder, and tell them in one line it's running. It writes `avatar.heygen.json` next to the video
(the video id, the script, their yes).

If it stops with "still being made": `<ken> --heygen get <video_id> --out videos/x/avatar.mp4` later. `status
<video_id>` is a quick look.

## 5. Watch it

Before they see it: the JSON says `vertical`, `width`, `height` and `seconds`. Grab three frames with ffmpeg and
look (face in frame, not cropped at the chin, nothing odd), and run `transcribe_media` on it to read the words back
against the script. A mispronounced name gets respelled in the script; making it again is a new video, so ask first.

## 6. Captions and b-roll: the video skill

`avatar.mp4` is now a normal talking video. Copy it to `videos/x/source.mp4` and follow the **video** skill:
transcribe, fix `source.words.json` to the script's exact words, plan, captions, graphics, render. Keep every word
(the script was already tight) and tell the plan it's an avatar: cover it with b-roll or their screen recordings
wherever the script talks about something they can show, so the avatar is on screen for the hook and the close and
less in between. Then send `reel.mp4` and wait for their yes.
