"""Ken's reels. A talking video is cut once with ffmpeg at the voice's real edges; then Remotion draws only
what has to look designed (each look of the captions, once, and the graphics for the seconds they're on screen)
and ffmpeg lays it over the footage with the framing, push-ins and grain in one pass. Everything drawn is kept by
what's in it, so a fix redraws only what changed. Remotion is set up the first time it's needed, never bundled:
its own Node, the kit's packages and the Chrome it draws with, all in <home>/tools (about 750 MB).

    ken --video-cut <folder>                     the numbered transcript of <folder>/source.*, to pick what to keep
    ken --video-cut <folder> 0-40,45-80 [--speed 1.1] [--no-zoom]
                                                 cut to those words, in the order said (or `all`): cut.mp4 and props.json
    ken --video-stills <folder> 1.5 9 24         a few frames of the reel, side by side in checks/sheet.jpg
    ken --video-render <folder> [--plan]         reel.mp4 from props.json (--plan: how much would be drawn)
    ken --video-setup                            set up Remotion now (safe to repeat)
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

NODE_VERSION = "22.20.0"
NODE_SHA256 = {  # from https://nodejs.org/dist/v22.20.0/SHASUMS256.txt
    "darwin-arm64": "cc04a76a09f79290194c0646f48fec40354d88969bec467789a5d55dd097f949",
    "darwin-x64": "00df9c5df3e4ec6848c26b70fb47bf96492f342f4bed6b17f12d99b3a45eeecc",
    "linux-arm64": "4181609e03dcb9880e7e5bf956061ecc0503c77a480c6631d868cb1f65a2c7dd",
    "linux-x64": "eeaccb0378b79406f2208e8b37a62479c70595e20be6b659125eb77dd1ab2a29",
}
SOURCE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "video" / "kit"


def node_dir(home: Path) -> Path:
    return home / "tools" / "node"


def kit_dir(home: Path) -> Path:
    # A machine built with the kit already installed (Ken's cloud computers) says where with KEN_VIDEO_KIT.
    return Path(os.environ["KEN_VIDEO_KIT"]) if os.environ.get("KEN_VIDEO_KIT") else home / "tools" / "remotion-kit"


def ready(home: Path) -> bool:
    if os.environ.get("KEN_VIDEO_KIT"):
        return (kit_dir(home) / "node_modules").is_dir()
    stamp = kit_dir(home) / ".ready"
    return stamp.exists() and stamp.read_text() == _kit_version()


def _kit_version() -> str:
    return hashlib.sha256((SOURCE / "package-lock.json").read_bytes()).hexdigest()[:16]


def _platform() -> str:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(platform.machine())
    if not system or not arch:
        raise SystemExit(f"Video tools aren't available on {platform.system()} {platform.machine()} yet.")
    return f"{system}-{arch}"


def _env(home: Path) -> dict:
    return {**os.environ, "PATH": f"{node_dir(home) / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "npm_config_update_notifier": "false", "npm_config_fund": "false", "npm_config_audit": "false"}


def _download(url: str, dest: Path, say) -> None:
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        total, done, shown = int(r.headers.get("Content-Length") or 0), 0, -1
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            pct = done * 100 // total if total else 0
            if pct // 25 != shown:
                shown = pct // 25
                say(f"  downloading Node… {pct}%")


def ensure_node(home: Path, say=print) -> Path:
    """Ken's own Node, so it never depends on (or changes) one they installed themselves."""
    node = node_dir(home) / "bin" / "node"
    if node.exists():
        return node
    plat = _platform()
    name = f"node-v{NODE_VERSION}-{plat}"
    node_dir(home).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=node_dir(home).parent) as tmp:
        archive = Path(tmp) / f"{name}.tar.gz"
        _download(f"https://nodejs.org/dist/v{NODE_VERSION}/{name}.tar.gz", archive, say)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != NODE_SHA256[plat]:
            raise SystemExit("The Node download didn't match its checksum, so it wasn't installed. Try again later.")
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")
        shutil.rmtree(node_dir(home), ignore_errors=True)
        (Path(tmp) / name).rename(node_dir(home))
    return node


def setup(home: Path, say=print) -> Path:
    """Node, the kit's packages and Remotion's Chrome. Each step is skipped when it's already done."""
    if ready(home):
        say(f"Video tools are ready: {kit_dir(home)}")
        return kit_dir(home)
    say("Setting up video tools (one time, a few minutes)…")
    ensure_node(home, say)
    kit = kit_dir(home)
    kit.mkdir(parents=True, exist_ok=True)
    for f in ("package.json", "package-lock.json"):
        shutil.copyfile(SOURCE / f, kit / f)
    npm = node_dir(home) / "bin" / "npm"
    say("  installing Remotion…")
    _run([str(npm), "ci", "--no-audit", "--no-fund", "--loglevel=error"], kit, home)
    say("  getting the browser Remotion renders with…")
    _run([str(node_dir(home) / "bin" / "npx"), "remotion", "browser", "ensure"], kit, home)
    (kit / ".ready").write_text(_kit_version())
    say(f"Video tools are ready: {kit}")
    return kit


def _run(cmd: list[str], cwd: Path, home: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, env=_env(home), capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{' '.join(Path(c).name for c in cmd[:2])} failed:\n{(r.stderr or r.stdout)[-2000:]}")


ENGINE = ("compose.mjs", "lib.mjs", "render.mjs", "stills.mjs", "tsconfig.json", "sfx")


def _project(home: Path, folder: Path, say=print) -> None:
    """The reel's Remotion project in its folder, using the kit's packages. The renderers are always the current
    ones; src/ is the reel's own code (Ken may add a graphic there), so it's copied once and then left alone."""
    kit = setup(home, say)
    template = SOURCE / "template"
    src = folder / "src"
    if src.exists() and not (src / "graphics").exists():  # from the older video tools (cards only)
        old = folder / "src-old"
        shutil.rmtree(old, ignore_errors=True)
        src.rename(old)
    for item in template.iterdir():
        dest = folder / item.name
        if item.name == "src":
            if not dest.exists():
                shutil.copytree(item, dest)
        elif item.name in ENGINE:
            if dest.is_dir():
                shutil.rmtree(dest)
            (shutil.copytree if item.is_dir() else shutil.copyfile)(item, dest)
    modules = folder / "node_modules"
    if not os.path.lexists(modules):
        modules.symlink_to(kit / "node_modules", target_is_directory=True)


# Cutting a talking video. Whisper's word times are rough (a word often "ends" before the voice
# does, or "starts" in the pause before it), so the edges come from the audio itself: each cut
# starts just before the voice does and ends after it fades, and a long pause inside what's kept
# is shortened, never removed, so the video keeps breathing.
HOP = 0.02              # loudness is measured every 20 ms
QUIET = 0.2             # this much quiet is the end of a phrase
LEAD, TAIL = 0.15, 0.3  # kept before the voice starts and after it stops
END_HOLD = 1.0          # after the reel's very last word, so the ending settles instead of clipping
PAUSE = 1.5             # quiet longer than this inside a kept part is shortened...
KEEP_PAUSE = 0.7        # ...to this (a natural breath, so it doesn't feel like a cut)
FADE_IN, FADE_OUT = 0.02, 0.03  # audio fades at each join, to avoid clicks
FPS = 30


def _source(folder: Path) -> Path:
    found = [p for p in folder.glob("source.*") if p.suffix.lower() not in (".json", ".srt", ".txt")]
    if not found:
        raise SystemExit(f"Copy the video into {folder} as source.<ext> and run transcribe_media on it first.")
    words = found[0].with_suffix(".words.json")
    if not words.exists():
        raise SystemExit(f"Run transcribe_media on {found[0]} first (it writes {words.name}).")
    return found[0]


def _loud(src: Path) -> list[bool]:
    """Where someone is talking, every 20 ms, judged against this video's own background noise."""
    import numpy as np

    # Only the voice's frequencies, so wind and traffic don't count as talking.
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                          "-af", "highpass=f=150,lowpass=f=3500", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop = int(16000 * HOP)
    n = len(a) // hop
    db = 20 * np.log10(np.sqrt((a[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-5)
    floor, voice = np.percentile(db, 15), np.percentile(db, 90)
    loud = db > floor + 0.4 * (voice - floor)
    # Speech flickers between syllables: fill gaps under 100 ms, then drop blips under 60 ms.
    run = lambda x, k: np.convolve(x.astype(float), np.ones(k), "same")
    loud = run(run(loud, 5) > 0, 5) >= 5
    loud = run(run(loud, 3) >= 3, 3) > 0
    return [bool(x) for x in loud]


def _quiet_runs(loud: list[bool]) -> list[tuple[float, float]]:
    runs, start = [], None
    for i, on in enumerate(loud + [True]):
        if not on and start is None:
            start = i
        elif on and start is not None:
            if (i - start) * HOP >= QUIET:
                runs.append((start * HOP, i * HOP))
            start = None
    return runs


def _pieces(words: list[dict], keep: list[tuple[int, int]], quiet: list[tuple[float, float]], loud: list[bool],
            length: float) -> list[list]:
    """Source-time ranges to keep, [start, end, is a real cut]: whole phrases with soft edges, long pauses shortened."""
    out = []
    talking = lambda t: 0 <= int(t / HOP) < len(loud) and loud[int(t / HOP)]
    for a, b in keep:
        s, e = words[a]["s"], words[b]["e"]
        lo = words[a - 1]["e"] if a > 0 else 0.0           # never back into a word that's cut
        hi = words[b + 1]["s"] if b + 1 < len(words) else length
        # Start: just before the voice comes back after the quiet before the first word.
        before = [q for q in quiet if max(lo, s - 0.8) <= q[1] <= s + 0.15]
        start = max(lo, (before[-1][1] if before else s) - LEAD)
        # End: where the voice actually stops after the last word starts (Whisper often stretches a
        # word's end across the pause after it), plus a tail, but not into what comes next.
        after = [q for q in quiet if words[b]["s"] + 0.08 <= q[0] <= e + 0.8]
        tail = END_HOLD if (a, b) == keep[-1] else TAIL
        end = min(after[0][0] + tail, after[0][1] - 0.05) if after else max(e, min(e + tail, hi - 0.05))
        # If an edge still lands on the voice, slide it out into the quiet.
        for _ in range(25):
            if not talking(start) or start - HOP < lo + 0.02:
                break
            start -= HOP
        for _ in range(25):
            if not talking(end) or end + HOP > hi - 0.02:
                break
            end += HOP
        start, end = max(0.0, start), min(length, end)
        # Long pauses inside: keep a short breath of each side. Only the first piece of a range is a real cut.
        cur, first = start, True
        for q0, q1 in quiet:
            if q0 > cur + 0.1 and q1 < end - 0.1 and q1 - q0 > PAUSE:
                out.append([cur, q0 + KEEP_PAUSE / 2, first])
                cur, first = q1 - KEEP_PAUSE / 2, False
        out.append([cur, end, first])
    merged = []
    for p in out:  # in play order; only neighbours that touch in the source merge
        if merged and 0 <= p[0] - merged[-1][1] < 0.12:
            merged[-1][1] = max(merged[-1][1], p[1])
        else:
            merged.append(p)
    # A part that's only quiet (Whisper put a word in a pause) isn't worth a cut.
    silent = lambda a, b: any(q0 <= a + 0.05 and b - 0.05 <= q1 for q0, q1 in quiet)
    return [[round(a, 3), round(b, 3), real] for a, b, real in merged if b - a > 0.15 and not silent(a, b)]


def _hard_edges(pieces: list[list], loud: list[bool]) -> list[float]:
    """Source times where a cut lands while they're still talking: those sound abrupt."""
    talking = lambda a, b: sum(loud[int(a / HOP):int(b / HOP)]) > 0.5 * max(1, int((b - a) / HOP))
    hard = []
    for i, (a, b, _) in enumerate(pieces):
        if i and talking(a, a + 0.1):
            hard.append(a)
        if i < len(pieces) - 1 and talking(b - 0.1, b):
            hard.append(b)
    return hard


def _retime(t: float, pieces: list[list]) -> float:
    """A source time on the cut's timeline (a time between parts snaps to the nearest edge of the nearest part)."""
    pos = 0.0
    for a, b, _ in pieces:
        if a <= t <= b:
            return pos + t - a
        pos += b - a
    best, pos = None, 0.0
    for a, b, _ in pieces:
        gap = a - t if t < a else t - b
        if best is None or gap < best[0]:
            best = (gap, pos + min(max(t, a), b) - a)
        pos += b - a
    return best[1]


def _framing(pieces: list[list], speed: float) -> tuple[list[float], list[dict]]:
    """Where the jump cuts are, and each part's framing (normal or punched in). The framing comes from the part's
    own place in the source, not from how many cuts came before, so dropping one sentence doesn't change the look
    of everything after it; when two neighbours land on the same one, the later one takes a third."""
    cuts, framing, pos, raws = [], [], 0.0, []
    for a, b, real in pieces:
        if real or not framing:
            raw = int(hashlib.md5(f"{a:.2f}".encode()).hexdigest(), 16) % 2 if framing else 0
            scale = 1.06 if raw else 1.0
            if raws and raw == raws[-1]:
                scale = 1.1 if framing[-1]["scale"] != 1.1 else 1.03
            raws.append(raw)
            framing.append({"at": round(pos / speed, 4), "scale": scale})
            if pos:
                cuts.append(round(pos / speed, 4))
        pos += b - a
    return cuts, framing


def _listing(words: list[dict]) -> str:
    lines, cur = [], []
    for i, w in enumerate(words):
        if i and w["s"] - words[i - 1]["e"] >= 0.6:
            if cur:
                lines.append(cur)
            lines.append(f"   (pause {w['s'] - words[i - 1]['e']:.1f}s)")
            cur = []
        cur.append(i)
        if w["w"].endswith((".", "?", "!")):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return "\n".join(x if isinstance(x, str) else f"[{_stamp(words[x[0]]['s'])}] " + " ".join(f"{i}:{words[i]['w']}" for i in x)
                     for x in lines)


def _stamp(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def _parse_keep(spec: str, count: int) -> list[tuple[int, int]]:
    if spec.strip() == "all":
        return [(0, count - 1)]
    keep = []
    for part in spec.replace(" ", "").split(","):
        a, _, b = part.partition("-")
        a, b = int(a), int(b or a)
        if not 0 <= a <= b < count:
            raise SystemExit(f"{part}: words are numbered 0 to {count - 1}.")
        if keep and a <= keep[-1][1]:
            raise SystemExit(f"{part}: keep their order. Ranges go in the order they were said, without overlapping.")
        keep.append((a, b))
    return keep


def cut(folder: Path, spec: str | None, say=print, speed: float = 1.0, zoom: bool = True) -> None:
    """Without `spec`, the numbered transcript to choose from. With it, cut.mp4 (vertical 1080x1920, 30 fps,
    loudness-normalised) and props.json with every kept word timed on it, keeping anything else already there."""
    src = _source(folder)
    words = json.loads(src.with_suffix(".words.json").read_text())
    if not spec:
        say(_listing(words))
        say(f"\nCut with: --video-cut {folder} <word ranges, like 0-40,45-80, or all> [--speed 1.1]")
        return
    if not 0.8 <= speed <= 1.3:
        raise SystemExit("--speed should be between 0.8 and 1.3; faster sounds rushed.")
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                                  capture_output=True, text=True, check=True).stdout.strip())
    keep = _parse_keep(spec, len(words))
    loud = _loud(src)
    pieces = _pieces(words, keep, _quiet_runs(loud), loud, length)
    if not pieces:
        raise SystemExit("Nothing left to keep.")
    # Whole frames per part, so every cut lands exactly on a frame.
    pieces = [[a, round(min(length, a + max(1, round((b - a) / speed * FPS)) / FPS * speed), 4), real] for a, b, real in pieces]
    inputs, graph = [], []
    for i, (a, b, _) in enumerate(pieces):
        inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src)]
        # Each part starts at zero (a seek lands a little after it), so joining them leaves no missing frames.
        # Phones tag their videos "rotate 90°"; the frames come out upright, so the tag is dropped.
        graph.append(f"[{i}:v]setpts=(PTS-STARTPTS)/{speed},fps={FPS},scale=1080:1920:force_original_aspect_ratio=increase,"
                     f"crop=1080:1920,setsar=1,format=yuv420p,sidedata=delete:type=DISPLAYMATRIX[v{i}]")
        graph.append(f"[{i}:a]asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo:sample_rates=48000,atempo={speed},"
                     f"afade=t=in:d={FADE_IN},afade=t=out:st={max(0, (b - a) / speed - FADE_OUT):.3f}:d={FADE_OUT}[a{i}]")
    graph.append("".join(f"[v{i}][a{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=1[v][ac]")
    graph.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo[a]")
    out = folder / "cut.mp4"
    say(f"Cutting {src.name} into {len(pieces)} parts…")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]",
                        "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"ffmpeg failed:\n{r.stderr[-2000:]}")

    # Every word in a kept range is on the new timeline, so nothing they say goes uncaptioned. Fixes made to the
    # words earlier (spelling, emphasis) carry over when a reel is cut again.
    props_path = folder / "props.json"
    props = json.loads(props_path.read_text()) if props_path.exists() else {}
    before = {w["i"]: w for w in props.get("words", []) if "i" in w}
    timed = []
    for i in dict.fromkeys(i for a, b in keep for i in range(a, b + 1)):
        s, e = _retime(words[i]["s"], pieces) / speed, _retime(words[i]["e"], pieces) / speed
        word = {"text": before.get(i, {}).get("text", words[i]["w"]), "start": round(s, 3), "end": round(max(e, s + 0.08), 3), "i": i}
        if before.get(i, {}).get("emph"):
            word["emph"] = True
        timed.append(word)
    cuts, framing = _framing(pieces, speed)
    duration = sum(b - a for a, b, _ in pieces) / speed
    props.update({"video": "cut.mp4", "durationSeconds": round(duration, 4), "words": timed, "cuts": cuts, "framing": framing})
    if not zoom:
        props["style"] = {**props.get("style", {}), "zoomCuts": False}
    props_path.write_text(json.dumps(props, indent=1))
    (folder / "cutlist.json").write_text(json.dumps({"keep": spec, "speed": speed, "pieces": [[a, b] for a, b, _ in pieces]}))
    say(f"{out}: {duration:.1f}s from {length:.1f}s. Its words, timed on the new video, are in {props_path}"
        " (fix any misheard word there).")
    say("\nThe reel's script (/ marks a cut):\n" + " / ".join(" ".join(words[i]["w"] for i in range(a, b + 1)) for a, b in keep) + "\n")
    hard = _hard_edges(pieces, loud)
    if hard:
        near = lambda t: min(range(len(words)), key=lambda i: min(abs(words[i]["s"] - t), abs(words[i]["e"] - t)))
        say("These cuts land mid-speech and will sound abrupt; cut at a pause instead, or keep the word: "
            + ", ".join(f"near {near(t)}:{words[near(t)]['w']} ({_stamp(t)})" for t in hard))


def _props(folder: Path) -> dict:
    path = folder / "props.json"
    if not path.exists():
        if (folder / "reel.json").exists():
            raise SystemExit("This video was made with Ken's older video tools. Cut it again with --video-cut "
                             "(the same ranges) to switch it to the new ones, then move its graphics into props.json.")
        raise SystemExit(f"No {path}: cut the video first with --video-cut.")
    return json.loads(path.read_text())


def _node(home: Path, folder: Path, args: list[str]) -> int:
    """Runs one of the kit's scripts in the reel's folder, its output going straight to Ken."""
    node = node_dir(home) / "bin" / "node"
    return subprocess.run([str(node) if node.exists() else "node", *args], cwd=folder, env=_env(home)).returncode


def render(home: Path, folder: Path, say=print, plan: bool = False) -> Path:
    """reel.mp4, ready to post. The fast way checks its own result; if that fails, or the reel has whip pans
    (chapters), which only the slow way does, Chrome draws the whole reel instead: slower, always right."""
    props = _props(folder)
    _project(home, folder, say)
    out = folder / "reel.mp4"
    extra = ["--plan"] if plan else []
    if props.get("chapters"):
        code = _node(home, folder, ["render.mjs", str(folder), str(out), *extra])
    else:
        code = _node(home, folder, ["compose.mjs", str(folder), str(out), *extra])
        if code and not plan:
            say("The fast render didn't check out; drawing the whole reel in Chrome instead (slower).")
            code = _node(home, folder, ["render.mjs", str(folder), str(out)])
    if code:
        raise SystemExit("Rendering failed; the lines above say why.")
    return out


def stills(home: Path, folder: Path, times: list[str], say=print) -> None:
    """A few frames of the reel without rendering it, side by side in checks/sheet.jpg."""
    _props(folder)
    _project(home, folder, say)
    if not times or _node(home, folder, ["stills.mjs", str(folder), *times]):
        raise SystemExit("Usage: --video-stills <folder> <seconds...>")


def main(argv: list[str], home: Path) -> None:
    say = lambda line: print(line, flush=True)
    folder = lambda: Path(os.path.expanduser(argv[1])).resolve()
    if argv[0] == "--video-setup":
        setup(home, say)
    elif argv[0] == "--video-cut" and len(argv) > 1:
        rest, speed = argv[2:], 1.0
        if "--speed" in rest:
            i = rest.index("--speed")
            speed = float(rest[i + 1])
            del rest[i:i + 2]
        zoom = "--no-zoom" not in rest
        cut(folder(), " ".join(a for a in rest if a != "--no-zoom") or None, say, speed=speed, zoom=zoom)
    elif argv[0] == "--video-render" and len(argv) > 1:
        render(home, folder(), say, plan="--plan" in argv[2:])
    elif argv[0] == "--video-stills" and len(argv) > 1:
        stills(home, folder(), argv[2:], say)
    else:
        raise SystemExit(__doc__)


def path_entry(home: Path) -> str:
    return str(node_dir(home) / "bin")


if __name__ == "__main__":
    main(sys.argv[1:], Path(os.environ.get("KEN_HOME", Path.home() / ".ken")))
