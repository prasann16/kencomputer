"""Ken's reels. The footage is cut once with ffmpeg; captions are drawn by ffmpeg; only the cards go
through Remotion, each rendered once and kept, so changing one card or the captions re-renders seconds,
not the whole video. Remotion is set up the first time a video has cards, never bundled: its own Node,
the kit's packages and the Chrome it renders with, all in <home>/tools (about 750 MB).

    ken --video-cut <folder>               the numbered transcript of <folder>/source.*, to pick what to keep
    ken --video-cut <folder> 0-40,45-80    cut it to those words (or `all`): public/cut.mp4 and reel.json
    ken --video-render <folder>            reel.mp4 from reel.json: cut footage, captions and cards
    ken --video-setup                      set up Remotion now (safe to repeat)
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
    return home / "tools" / "remotion-kit"


def ready(home: Path) -> bool:
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


def _project(home: Path, folder: Path, say=print) -> None:
    """The cards' Remotion code (src/) in a video's folder, using the kit's packages."""
    kit = setup(home, say)
    for item in (SOURCE / "template").iterdir():
        dest = folder / item.name
        if not dest.exists():  # never overwrite their edits
            (shutil.copytree if item.is_dir() else shutil.copyfile)(item, dest)
    modules = folder / "node_modules"
    if not os.path.lexists(modules):
        modules.symlink_to(kit / "node_modules", target_is_directory=True)


# Cutting a talking video. Whisper's word times are rough (a word often "ends" before the voice
# does, or "starts" in the pause before it), so the edges come from the audio itself: each cut
# starts just before the voice does and ends after it fades, and a long pause inside what's kept
# is shortened, never removed, so the video keeps breathing.
HOP = 0.02            # loudness is measured every 20 ms
QUIET = 0.2           # this much quiet is the end of a phrase
LEAD, TAIL = 0.15, 0.3  # kept before the voice starts and after it stops
PAUSE = 0.9           # quiet longer than this inside a kept part is shortened...
KEEP_PAUSE = 0.45     # ...to this
SIZE = (1080, 1920)
ZOOM = 1.12           # every other part is punched in a little, so jump cuts look deliberate


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

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop = int(16000 * HOP)
    n = len(a) // hop
    db = 20 * np.log10(np.sqrt((a[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-5)
    floor, voice = np.percentile(db, 15), np.percentile(db, 90)
    return list(db > floor + 0.4 * (voice - floor))


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


def _pieces(words: list[dict], keep: list[tuple[int, int]], quiet: list[tuple[float, float]], length: float):
    """Source-time ranges to keep: whole phrases with soft edges, long pauses shortened."""
    out = []
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
        end = min(after[0][0] + TAIL, after[0][1] - 0.05) if after else min(e + 0.12, max(hi, e))
        start, end = max(0.0, start), min(length, end)
        # Long pauses inside: keep a short breath of each side.
        cur = start
        for q0, q1 in quiet:
            if q0 > cur + 0.1 and q1 < end - 0.1 and q1 - q0 > PAUSE:
                out.append([cur, q0 + KEEP_PAUSE / 2])
                cur = q1 - KEEP_PAUSE / 2
        out.append([cur, end])
    out.sort()
    merged = []
    for p in out:
        if merged and p[0] - merged[-1][1] < 0.12:  # too close to be worth a cut
            merged[-1][1] = max(merged[-1][1], p[1])
        else:
            merged.append(p)
    # A part that's only quiet (Whisper put a word in a pause) isn't worth a cut.
    silent = lambda a, b: any(q0 <= a + 0.05 and b - 0.05 <= q1 for q0, q1 in quiet)
    return [[round(a, 3), round(b, 3)] for a, b in merged if b - a > 0.15 and not silent(a, b)]


def _hard_edges(pieces: list[list[float]], loud: list[bool]) -> list[float]:
    """Source times where a cut lands while they're still talking: those sound abrupt."""
    talking = lambda a, b: sum(loud[int(a / HOP):int(b / HOP)]) > 0.5 * max(1, int((b - a) / HOP))
    hard = []
    for i, (a, b) in enumerate(pieces):
        if i and talking(a, a + 0.1):
            hard.append(a)
        if i < len(pieces) - 1 and talking(b - 0.1, b):
            hard.append(b)
    return hard


def _retime(t: float, pieces: list[list[float]]) -> float:
    """A source time on the cut's timeline (a time inside something cut out snaps to the next part)."""
    pos = 0.0
    for a, b in pieces:
        if t < a:
            return pos
        if t <= b:
            return pos + t - a
        pos += b - a
    return pos


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
    stamp = lambda t: f"{int(t // 60)}:{t % 60:04.1f}"
    return "\n".join(x if isinstance(x, str) else f"[{stamp(words[x[0]]['s'])}] " + " ".join(f"{i}:{words[i]['w']}" for i in x)
                     for x in lines)


def _parse_keep(spec: str, count: int) -> list[tuple[int, int]]:
    if spec.strip() == "all":
        return [(0, count - 1)]
    keep = []
    for part in spec.replace(" ", "").split(","):
        a, _, b = part.partition("-")
        a, b = int(a), int(b or a)
        if not 0 <= a <= b < count:
            raise SystemExit(f"{part}: words are numbered 0 to {count - 1}.")
        keep.append((a, b))
    return keep


def cut(folder: Path, spec: str | None, say=print, zoom: bool = True) -> None:
    """Without `spec`, the numbered transcript to choose from. With it, public/cut.mp4 (vertical
    1080x1920, 30 fps) and the words on its timeline in cut.json, cut.srt and reel.json if there."""
    src = _source(folder)
    words = json.loads(src.with_suffix(".words.json").read_text())
    if not spec:
        say(_listing(words))
        say(f"\nCut with: --video-cut {folder} <word ranges, like 0-40,45-80, or all>")
        return
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                                  capture_output=True, text=True, check=True).stdout.strip())
    keep = _parse_keep(spec, len(words))
    loud = _loud(src)
    pieces = _pieces(words, keep, _quiet_runs(loud), length)
    w, h = SIZE
    graph = []
    for i, (a, b) in enumerate(pieces):
        punch = ""
        if zoom and i % 2:
            zw, zh = round(w * ZOOM / 2) * 2, round(h * ZOOM / 2) * 2
            punch = f",scale={zw}:{zh},crop={w}:{h}:{(zw - w) // 2}:{int((zh - h) * 0.42)}"
        graph.append(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS,fps=30,scale={w}:{h}:force_original_aspect_ratio=increase,"
                     f"crop={w}:{h}{punch},setsar=1,sidedata=delete:type=DISPLAYMATRIX[v{i}]")
        graph.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,afade=t=out:st={b - a - 0.03:.3f}:d=0.03[a{i}]")
    graph.append("".join(f"[v{i}][a{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=1[v][a0]")
    graph.append("[a0]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    out = folder / "public" / "cut.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    say(f"Cutting {src.name} into {len(pieces)} parts…")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", ";".join(graph), "-map", "[v]",
                        "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", str(out)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"ffmpeg failed:\n{r.stderr[-2000:]}")
    # Every word in a kept range is on the new timeline, so nothing they say goes uncaptioned.
    kept = sorted({i for a, b in keep for i in range(a, b + 1)})
    timed = []
    for i in kept:
        s, e = _retime(words[i]["s"], pieces), _retime(words[i]["e"], pieces)
        timed.append({"w": words[i]["w"], "s": round(s, 3), "e": round(max(e, s + 0.08), 3)})
    bounds, pos = [], 0.0
    for a, b in pieces:
        bounds.append([round(pos, 3), round(pos + b - a, 3)])
        pos += b - a
    reel = folder / "reel.json"
    data = json.loads(reel.read_text()) if reel.exists() else {"style": DEFAULT_STYLE, "scenes": []}
    data.update({"video": "cut.mp4", "duration": round(pos, 3), "cuts": bounds, "words": timed})
    reel.write_text(json.dumps(data, indent=1))
    say(f"{out}: {pos:.1f}s, from {length:.1f}s. Its words, timed on the new video, are in {reel}"
        " (fix any misheard word there).")
    hard = _hard_edges(pieces, loud)
    if hard:
        near = lambda t: min(range(len(words)), key=lambda i: min(abs(words[i]["s"] - t), abs(words[i]["e"] - t)))
        say("These cuts land mid-speech and will sound abrupt; cut at a pause instead, or keep the word: "
            + ", ".join(f"near {near(t)}:{words[near(t)]['w']} ({int(t // 60)}:{t % 60:04.1f})" for t in hard))


# Rendering. Captions are an .ass subtitle file ffmpeg draws (seconds, not minutes); each card is a
# short transparent clip from Remotion, kept under .cards/ by a hash of what it shows, so only cards
# that changed are rendered again; one ffmpeg pass lays everything on the cut footage.
DEFAULT_STYLE = {"font": "Montserrat", "accent": "#FFD60A",
                 "captions": {"top": 1330, "size": 82, "uppercase": True, "perPage": 3}}
FONTS = {"Montserrat": "Montserrat Black", "Inter": "Inter Black", "Poppins": "Poppins Black",
         "Bebas Neue": "Bebas Neue", "Instrument Serif": "Instrument Serif"}


def _ass_colour(hex_: str, alpha: int = 0) -> str:
    h = hex_.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _pages(words: list[dict], most: int) -> list[list[dict]]:
    """A few words at a time: a new page after `most` words, a pause, or the end of a sentence."""
    pages, cur = [], []
    for i, w in enumerate(words):
        prev = words[i - 1] if i else None
        if cur and (len(cur) >= most or (prev and w["s"] - prev["e"] > 0.3) or prev["w"].endswith((".", ",", "?", "!"))):
            pages.append(cur)
            cur = []
        cur.append(w)
    return pages + [cur] if cur else pages


def _ass(reel: dict) -> str:
    """Word-by-word captions: the page of words on screen, the one being said in the accent colour."""
    style = {**DEFAULT_STYLE, **reel.get("style", {})}
    cap = {**DEFAULT_STYLE["captions"], **style.get("captions", {})}
    w, h = reel.get("width", SIZE[0]), reel.get("height", SIZE[1])
    font = FONTS.get(style.get("font"), FONTS["Montserrat"])
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: Cap,{font},{round(cap['size'] * 1.2)},{_ass_colour(style.get('text', '#FFFFFF'))},&H00FFFFFF,&H00000000,&H66000000,"
            f"0,0,0,0,100,100,2,0,1,5,3,8,50,50,{cap['top']},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    if cap.get("show") is False:
        return head
    stamp = lambda t: f"{int(t // 3600)}:{int(t % 3600 // 60):02}:{t % 60:05.2f}"
    clean = lambda s: s.replace("{", "").replace("}", "").replace("\\", "").rstrip(",.")
    accent, end_at = "&H" + _ass_colour(style["accent"])[4:], (reel.get("end") or {}).get("from", 1e9)
    pages, events = _pages(reel.get("words", []), cap["perPage"]), []
    for n, page in enumerate(pages):
        start = page[0]["s"] - 0.05
        stop = min(pages[n + 1][0]["s"] - 0.05 if n + 1 < len(pages) else page[-1]["e"] + 0.4, end_at)
        for k, word in enumerate(page):
            a = start if k == 0 else word["s"] - 0.03
            b = page[k + 1]["s"] - 0.03 if k + 1 < len(page) else stop
            if b <= a:
                continue
            parts = []
            for j, x in enumerate(page):
                text = clean(x["w"]).upper() if cap.get("uppercase", True) else clean(x["w"])
                if j == k:
                    parts.append(f"{{\\c{accent}&\\fscx108\\fscy108}}{text}{{\\r}}")
                elif j > k:
                    parts.append(f"{{\\alpha&H70&}}{text}{{\\r}}")
                else:
                    parts.append(text)
            pop = "{\\fscx92\\fscy92\\t(0,110,\\fscx100\\fscy100)}" if k == 0 else ""
            events.append(f"Dialogue: 0,{stamp(a)},{stamp(b)},Cap,,0,0,0,,{pop}{' '.join(parts)}")
    return head + "\n".join(events) + "\n"


def _cards(reel: dict, fps: int) -> list[dict]:
    """Each card (and the end card) as its own clip: where it sits and what it shows."""
    total = max(1, int(reel["duration"] * fps))
    out = []
    for i, s in enumerate(reel.get("scenes", [])):
        out.append({"only": i, "data": s, "a": max(0, int(s["from"] * fps)), "b": min(total - 1, int(s["to"] * fps))})
    if reel.get("end"):
        out.append({"only": "end", "data": reel["end"], "a": int(reel["end"]["from"] * fps), "b": total - 1})
    return [c for c in out if c["b"] > c["a"]]


def _code_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted((folder / "src").rglob("*")):
        if f.is_file():
            h.update(f.name.encode() + f.read_bytes())
    for f in sorted((folder / "public").iterdir()):
        if f.is_file() and f.name != "cut.mp4":  # images a card shows
            h.update(f.name.encode() + str(f.stat().st_mtime_ns).encode())
    return h.hexdigest()


def render(home: Path, folder: Path, say=print) -> Path:
    """reel.mp4: the cut footage with captions and cards, ready to post."""
    import time

    started = time.monotonic()
    reel = json.loads((folder / "reel.json").read_text())
    footage = folder / "public" / reel.get("video", "cut.mp4")
    if not footage.exists():
        raise SystemExit(f"No {footage}: cut the video first with --video-cut.")
    fps = reel.get("fps", 30)
    cards, made = _cards(reel, fps), 0
    if cards:
        _project(home, folder, say)
        (folder / ".cards").mkdir(exist_ok=True)
        code = _code_hash(folder)
        style = {k: v for k, v in reel.get("style", {}).items() if k != "captions"}  # captions aren't on cards
        size = [reel.get("width", SIZE[0]), reel.get("height", SIZE[1]), fps, reel["duration"]]
        for c in cards:
            key = hashlib.sha256(json.dumps([c["data"], c["a"], c["b"], style, size, code], sort_keys=True).encode()).hexdigest()[:20]
            c["clip"] = folder / ".cards" / f"{key}.mov"
            if c["clip"].exists():
                continue
            say(f"  rendering card {c['only']} ({(c['b'] - c['a'] + 1) / fps:.1f}s)…")
            tmp = c["clip"].with_suffix(".part.mov")
            _run([str(node_dir(home) / "bin" / "npx"), "remotion", "render", "src/index.ts", "Cards", str(tmp),
                  f"--props={json.dumps({'only': c['only']})}", f"--frames={c['a']}-{c['b']}", "--codec=prores",
                  "--prores-profile=4444", "--pixel-format=yuva444p10le", "--image-format=png",
                  f"--concurrency={os.cpu_count() or 4}", "--log=error"], folder, home)
            tmp.rename(c["clip"])
            made += 1
        keep = {c["clip"].name for c in cards}
        for old in (folder / ".cards").glob("*.mov"):
            if old.name not in keep:
                old.unlink()
    (folder / "captions.ass").write_text(_ass(reel))
    fonts = folder / ".fonts"
    fonts.mkdir(exist_ok=True)
    for f in (SOURCE / "fonts").glob("*.ttf"):
        if not (fonts / f.name).exists():
            shutil.copyfile(f, fonts / f.name)
    # Phones tag their videos "rotate 90°"; the cut is already upright, so drop any tag it carried over.
    cmd, graph, last = ["ffmpeg", "-v", "error", "-y", "-noautorotate", "-i", str(footage)], [], "0:v"
    for n, c in enumerate(cards, 1):
        cmd += ["-itsoffset", f"{c['a'] / fps:.3f}", "-i", str(c["clip"])]
        graph.append(f"[{last}][{n}:v]overlay=eof_action=pass:format=auto[o{n}]")
        last = f"o{n}"
    graph.append(f"[{last}]ass=captions.ass:fontsdir=.fonts,format=yuv420p,sidedata=delete:type=DISPLAYMATRIX[v]")
    out = folder / "reel.mp4"
    cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "0:a?", "-c:a", "copy", "-movflags", "+faststart"]
    # A Mac's hardware encoder is about twice as fast as x264 here; x264 where there isn't one.
    hardware = ["-c:v", "h264_videotoolbox", "-b:v", "10M", "-maxrate", "12M", "-bufsize", "20M", "-profile:v", "high"]
    software = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "10M", "-bufsize", "20M"]
    for enc in ([hardware] if sys.platform == "darwin" else []) + [software]:
        r = subprocess.run(cmd + enc + [str(out)], cwd=folder, capture_output=True, text=True)
        if not r.returncode:
            break
    else:
        raise SystemExit(f"ffmpeg failed:\n{r.stderr[-2000:]}")
    say(f"{out}: {reel['duration']:.1f}s, made in {time.monotonic() - started:.0f}s "
        f"({made} card{'s' if made != 1 else ''} rendered, {len(cards) - made} reused).")
    return out


def main(argv: list[str], home: Path) -> None:
    say = lambda line: print(line, flush=True)
    if argv[0] == "--video-setup":
        setup(home, say)
    elif argv[0] == "--video-cut" and len(argv) > 1:
        rest = [a for a in argv[2:] if a != "--no-zoom"]
        cut(Path(os.path.expanduser(argv[1])).resolve(), " ".join(rest) or None, say, zoom="--no-zoom" not in argv)
    elif argv[0] == "--video-render" and len(argv) > 1:
        render(home, Path(os.path.expanduser(argv[1])).resolve(), say)
    else:
        raise SystemExit(__doc__)


def path_entry(home: Path) -> str:
    return str(node_dir(home) / "bin")


if __name__ == "__main__":
    main(sys.argv[1:], Path(os.environ.get("KEN_HOME", Path.home() / ".ken")))
