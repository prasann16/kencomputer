---
name: video-kit
description: Voice, audio and language tools for their videos, using ElevenLabs (Ken's own account on hosted Ken, their key elsewhere) plus ffmpeg. Dub a video into another language in the same voice, clone their own voice, write a new script in their own style and voice it (other languages too), voiceovers from a script, fix a wrong word without reshooting, change a voice, clean noisy audio, sound effects, background music, a new designed voice, captions/subtitles (SRT) in any language including Hinglish, find and write hooks, and cut a long video into short clips. Use when they ask to dub, translate, clone my voice, a script in my style, voiceover, read this in my voice, fix a word, clean/denoise the audio, remove background noise, add a sound effect, make music, add captions or subtitles, find hooks, or clip a podcast. For a full edited reel with graphics, the video skill does the edit and this kit adds the voice and sound.
---

# Video kit

One kit for everything around a video that isn't the edit itself: voices, sound, languages, captions, hooks and
clips. The **video** skill makes the reel (cut, captions, graphics, render); this kit feeds it and finishes it.
Work in `videos/<short-name>/` in your workspace, the same folder the video skill uses, and never change their
original file: every output is a new file next to it.

The ElevenLabs helper is `<ken> --eleven <command> ...`, where `<ken>` is the exact command in your instructions
(the same one the video skill uses). Every command prints one JSON line: the files it wrote, and either
`quota_left` (Ken's account) or `usd` (their own key).
`<ken> --eleven -h` lists them all. Below, `eleven.py <command>` is short for `<ken> --eleven <command>`.

## Pick the flow

Read only the file the ask needs.

| They say something like | Flow |
|---|---|
| "dub this in Hindi", "make a Tamil version", "translate my reel", "same video in Spanish" | `flows/dub.md` |
| "write me a script in my style", "a new video like my usual ones", "say this in my voice in Hindi and Spanish" | `flows/script-in-my-style.md` |
| "clone my voice", "read this in my voice", "voiceover for this", "I said the wrong price, fix it", "make it sound like a different voice", "make me a narrator voice" | `flows/voice.md` |
| "clean the audio", "remove the fan noise", "add a whoosh", "background music", "a sound for the transition" | `flows/sound.md` |
| "add captions", "subtitles", "SRT", "Hinglish captions", "captions in Hindi" | `flows/captions.md` |
| "find hooks", "better first 3 seconds", "why did this flop", "what hooks work in my niche" | `flows/hooks.md` |
| "clip this podcast", "10 shorts from this", "best moments", "make it vertical" | `flows/clips.md` |
| "edit this into a reel" (cut, graphics) | the **video** skill, then come back here for voice or sound |

Several at once ("clean the audio, add captions and a Hindi dub") run in this order: clean → cut (video skill) →
captions → dub. Dub last, from the finished English cut, so they pay for the minutes that stay.

## Rules for every flow

- **Whose account.** Run `eleven.py check` once per chat before the first ElevenLabs call. `"account": "ken"`
  means hosted Ken: it runs on Ken's own ElevenLabs account, included in their plan, with a monthly allowance per
  feature (`left`). They need no ElevenLabs account and no key: never ask for one. Anything else is their own key.
  If Ken's route isn't live on this computer yet, the helper uses their own `ELEVENLABS_API_KEY` when they added
  one (its JSON then says `"account": "own"`: their money, the own-key rules below), or says so in one line.
- **On Ken's account, no money talk.** Never mention dollars, prices, credits or plans. The only number they hear
  is what's left, when it matters: before a dub or anything long, `eleven.py estimate <feature> <file>` and say it
  in one line ("that uses 3 of your 9 dub minutes this month"), then go; a small thing just runs. When `fits` is
  false or a call says the allowance is used up, say what's left and when it comes back, once. Only if they say
  they have their own ElevenLabs key: it runs on that with `KEN_ELEVEN=own` in front of the command.
- **On their own key** (not hosted Ken): it reads `ELEVENLABS_API_KEY` from the environment. Never print it, echo
  it, write it to a file or put it in a command line. If it's missing, the helper says so: tell them in one line to
  add it in the Brain tab under Accounts & passwords, named `ELEVENLABS_API_KEY` (elevenlabs.io → Developers → API
  keys), and that it works from their next message. It's their money: before anything over about $0.50, or any
  dub, tell them the estimate in one line and wait for a yes. Prices are in `reference.md`.
- Hooks, clips and free captions don't need ElevenLabs: do those anyway.
- **Their own voice only.** Clone only the voice of the person you're talking to, after one yes in chat
  (`flows/voice.md`), which the helper records. On Ken's account a clone is theirs alone (nobody else can use it),
  they keep 3 at most (`eleven.py voice-delete <id>` frees one), and the free trial has none. Never a celebrity, a
  friend, a client or anyone "who said it's fine" unless that person says yes to you themselves: refuse in one
  friendly line and offer a stock or designed voice instead.
- **Label synthetic media.** A dubbed, cloned or changed voice is AI generated. When they're about to post one,
  remind them in one line to switch on the platform's AI label (India's IT Rules require it since Feb 2026; Meta,
  YouTube and TikTok all have the toggle). Don't nag about cleaned audio, music or sound effects.
- **Only their content.** Dub, clip and clean videos they made or have rights to. Someone else's podcast or reel
  is a copyright claim waiting to happen: say so once and stop.
- **Show, then post.** Send the file and wait for their yes before anything is posted anywhere.
- **Long work in the background.** A dub, music over a minute, or clipping a long video takes minutes: hand it to a
  background job with the folder, the exact commands and what they asked, and tell them in one line it's running.
- **Fail once, plainly.** If ElevenLabs or Ken says no (allowance used up, ElevenLabs down, file too big), the
  helper explains why. Tell them in one line with the fix; never retry in a loop.
- **Listen to what you made** before sending: check the length with ffprobe, and for a voice, read the words back
  with `transcribe_media` on the output. A dub or voiceover with a mispronounced name gets fixed first.
