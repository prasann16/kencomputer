#!/usr/bin/env python3
"""What's working in a niche, measured: find short videos, fetch the ones worth a look, measure each one
(hook frames, cut pace, on-screen text, speech, length, outlier score) and lay them out as a trend board.

    viral.py find  <dir> --accounts karaandnate,lostleblanc [--per-account 3]
    viral.py find  <dir> --niche travel [--tags iceland,solotravel] [--limit 30]
    viral.py fetch <dir> <url>... [--no-baseline]
    viral.py analyze <dir>            (every fetched reel that has no signals.json yet)
    viral.py board <dir> [--niche travel]

Standard library only, plus ffmpeg/ffprobe. yt-dlp (or the yt_dlp module) for links, tesseract and
faster-whisper when they're installed. The judgement (hook type, format, why it worked) is the AI's: this
fills in what can be measured and leaves those fields empty in signals.json."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Hashtags per niche: a start, not a list to trust. Generic tags ("#travel") pull in memes and other
# languages ("madeira" is also Portuguese for wood), so the AI checks every pick by its frames.
NICHES = {
    "travel": ["solotravel", "traveltips", "travelvlog", "traveltok", "hiddengems", "travelguide"],
    "lifestyle": ["dayinmylife", "morningroutine", "lifestylevlog", "slowliving", "thatgirl"],
    "food": ["streetfood", "foodvlog", "whatieatinaday"],
}
JUDGED = {  # filled by the AI from the frames, transcript and caption; see SKILL.md for the choices
    "hook_type": None, "hook_text": None, "hook_why": None, "format": None, "text_style": None,
    "audio": None, "cta": None, "why_it_worked": None, "steal_this": None,
}


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ytdlp() -> list[str] | None:
    if shutil.which("yt-dlp"):
        return ["yt-dlp", "--no-update", "--no-warnings"]
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp", "--no-update", "--no-warnings"]
    except ImportError:
        return None


def ytdlp_json(args: list[str], timeout: int = 120) -> dict | None:
    base = ytdlp()
    if not base:
        return None
    try:
        r = run(base + ["-J", *args], timeout)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def slug(url: str) -> str:
    m = re.search(r"(?:shorts/|reel/|reels/|video/|v=|youtu\.be/|/p/)([\w-]{5,})", url)
    return m.group(1) if m else re.sub(r"\W+", "-", url)[-40:].strip("-")


def platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    for name in ("instagram", "tiktok", "youtube", "youtu.be", "pinterest", "facebook"):
        if name in host:
            return "youtube" if name == "youtu.be" else name
    return host or "file"


def median(xs: list[float]) -> float | None:
    xs = [x for x in xs if x]
    return statistics.median(xs) if len(xs) >= 5 else None


# ------------------------------------------------------------------ find

def find_youtube_api(key: str, niche: str, tags: list[str], limit: int) -> list[dict]:
    """YouTube Data API: search (own bucket of 100 calls a day) for short videos from the last 14 days,
    then videos.list (1 unit) for real counts."""
    after = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids: list[str] = []
    for q in [f"{niche} #shorts", *[f"#{t}" for t in tags[:2]]]:
        url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode({
            "part": "id", "type": "video", "videoDuration": "short", "order": "viewCount", "maxResults": 25,
            "publishedAfter": after, "q": q, "key": key})
        with urllib.request.urlopen(url, timeout=30) as r:
            ids += [i["id"]["videoId"] for i in json.load(r).get("items", []) if i["id"]["videoId"] not in ids]
    out = []
    for i in range(0, len(ids), 50):
        url = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode({
            "part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50]), "key": key})
        with urllib.request.urlopen(url, timeout=30) as r:
            for v in json.load(r).get("items", []):
                out.append({"url": f"https://www.youtube.com/shorts/{v['id']}", "title": v["snippet"]["title"],
                            "channel": v["snippet"]["channelTitle"], "views": int(v["statistics"].get("viewCount", 0)),
                            "source": "youtube-api"})
    return sorted(out, key=lambda c: -c["views"])[:limit]


def find_public(tags: list[str], per_tag: int) -> list[dict]:
    """Public YouTube hashtag Shorts pages through yt-dlp: no key needed, a prototype fallback."""
    out, seen = [], set()
    for t in tags:
        d = ytdlp_json(["--flat-playlist", "--playlist-end", str(per_tag), f"https://www.youtube.com/hashtag/{t}/shorts"])
        for e in (d or {}).get("entries") or []:
            if e.get("url") and e["url"] not in seen:
                seen.add(e["url"])
                out.append({"url": e["url"], "title": e.get("title") or "", "views": e.get("view_count") or 0,
                            "tag": t, "source": "youtube-hashtag-page"})
    return out


def find_accounts(handles: list[str], per_account: int) -> list[dict]:
    """The best source: accounts they look up to. Each account's recent short videos, scored against that
    account's own median, so a small creator's breakout counts as much as a big one's."""
    out = []
    for h in handles:
        h = h.strip().lstrip("@")
        page = h if h.startswith("http") else f"https://www.youtube.com/@{h}/shorts"
        d = ytdlp_json(["--flat-playlist", "--playlist-end", "40", page], timeout=180)
        entries = [e for e in (d or {}).get("entries") or [] if e.get("view_count")]
        med = median([e["view_count"] for e in entries[3:]])
        if not med:
            print(f"skip  @{h}  (no public short videos found)", file=sys.stderr)
            continue
        for e in sorted(entries, key=lambda e: -e["view_count"])[:per_account]:
            out.append({"url": e.get("url") or f"https://www.youtube.com/shorts/{e['id']}", "title": e.get("title") or "",
                        "views": e["view_count"], "account": (d or {}).get("channel") or h,
                        "account_median_views": med, "outlier": round(e["view_count"] / med, 1), "source": "account"})
    return sorted(out, key=lambda c: -c["outlier"])


def cmd_find(a) -> None:
    out = Path(a.dir)
    out.mkdir(parents=True, exist_ok=True)
    if a.accounts:
        found = find_accounts(a.accounts.split(","), a.per_account)
        (out / "candidates.json").write_text(json.dumps({"niche": a.niche, "accounts": a.accounts, "found": found}, indent=1))
        for c in found:
            print(f"{c['outlier']:>6}x  {c['views']:>11,}  {c['url']}  @{c['account']}  {c['title'][:60]}")
        print(f"\n{len(found)} candidates in {out / 'candidates.json'}")
        return
    tags = [t.strip().lstrip("#") for t in (a.tags or "").split(",") if t.strip()] or NICHES.get(a.niche, [a.niche])
    key = os.environ.get("YOUTUBE_API_KEY")
    found = []
    if key:
        try:
            found = find_youtube_api(key, a.niche, tags, a.limit)
        except Exception as e:  # quota or a bad key: say so, then fall back
            print(f"YouTube API failed ({e}); using public hashtag pages", file=sys.stderr)
    if not found:
        if not ytdlp():
            sys.exit("No YOUTUBE_API_KEY and no yt-dlp: ask them for links to reels they like instead.")
        found = find_public(tags, max(8, a.limit // max(1, len(tags)) + 4))
    found.sort(key=lambda c: -(c["views"] or 0))
    found = found[:a.limit]
    (out / "candidates.json").write_text(json.dumps({"niche": a.niche, "tags": tags, "found": found}, indent=1))
    for c in found:
        print(f"{c['views'] or 0:>11,}  {c['url']}  {c['title'][:70]}")
    print(f"\n{len(found)} candidates in {out / 'candidates.json'}. Pick the ones really in the niche (check titles,"
          " then frames after fetch); generic tags pull in memes and other languages.")


# ------------------------------------------------------------------ fetch

def baseline(info: dict) -> tuple[float | None, int]:
    """The account's median views over its last ~30 short videos, where the platform shows them publicly."""
    plat, cid, user = platform(info.get("webpage_url") or ""), info.get("channel_id"), info.get("uploader_id")
    if plat == "youtube" and cid:
        page = f"https://www.youtube.com/channel/{cid}/shorts"
    elif plat == "tiktok" and user:
        page = f"https://www.tiktok.com/@{user.lstrip('@')}"
    else:
        return None, 0  # Instagram: only through the Graph API's business discovery (see docs)
    d = ytdlp_json(["--flat-playlist", "--playlist-end", "33", page], timeout=120)
    # The newest few are still collecting views and would make every older reel look like a hit.
    views = [e.get("view_count") for e in ((d or {}).get("entries") or [])[3:] if e.get("view_count")]
    return median(views), len(views)


def cmd_fetch(a) -> None:
    root = Path(a.dir)
    base = ytdlp()
    for url in a.urls:
        d = root / "reels" / slug(url)
        d.mkdir(parents=True, exist_ok=True)
        if Path(url).is_file():  # a file they sent: nothing to download, no public numbers
            shutil.copy2(url, d / ("video" + Path(url).suffix))
            (d / "meta.json").write_text(json.dumps({"url": str(url), "platform": "file"}, indent=1))
            print(f"ok    {d.name}  (their file)")
            continue
        if not base:
            sys.exit("yt-dlp isn't installed: ask them to send the video file, or install yt-dlp.")
        info = ytdlp_json(["--skip-download", url])
        if not info:
            print(f"skip  {url}  (couldn't read it: private, login only, or removed; ask them for the file)")
            continue
        r = run(base + ["-f", "bv*[height<=1280]+ba/b[height<=1280]/b", "--merge-output-format", "mp4",
                        "-o", str(d / "video.%(ext)s"), url], timeout=600)
        if r.returncode != 0:
            print(f"skip  {url}  ({r.stderr.strip().splitlines()[-1][:120] if r.stderr.strip() else 'download failed'})")
            continue
        med, n = (None, 0) if a.no_baseline else baseline(info)
        views = info.get("view_count")
        meta = {
            "url": url, "platform": platform(url), "title": info.get("title"),
            "caption": (info.get("description") or "")[:2200], "account": info.get("channel") or info.get("uploader"),
            "account_id": info.get("channel_id") or info.get("uploader_id"),
            "followers": info.get("channel_follower_count"), "views": views, "likes": info.get("like_count"),
            "comments": info.get("comment_count"), "duration": info.get("duration"),
            "posted": info.get("upload_date"), "sound": " - ".join(x for x in (info.get("track"), info.get("artist")) if x) or None,
            "tags": info.get("tags") or [], "account_median_views": med, "baseline_videos": n,
            "outlier": round(views / med, 1) if views and med else None,
            "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (d / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
        print(f"ok    {d.name}  views={views or '?'}  outlier={meta['outlier'] or '?'}x  {(meta['title'] or '')[:60]}")


# ------------------------------------------------------------------ analyze

def probe(video: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
             "-of", "json", str(video)])
    d = json.loads(r.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    num, _, den = (v.get("r_frame_rate") or "0/1").partition("/")
    return {"duration": round(float(d.get("format", {}).get("duration", 0)), 2), "width": v.get("width"),
            "height": v.get("height"), "fps": round(float(num) / float(den or 1), 2) if float(den or 1) else None,
            "has_audio": any(s.get("codec_type") == "audio" for s in d.get("streams", []))}


def cuts(video: Path, threshold: float = 0.3) -> list[float]:
    r = run(["ffmpeg", "-v", "info", "-i", str(video), "-an", "-vf",
             f"scale=240:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"], timeout=600)
    return [round(float(t), 2) for t in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def frames(video: Path, out: Path, times: list[float], name: str, cols: int) -> Path:
    """One image with a frame at each time, labelled, for the AI to look at."""
    tmp = out / f".{name}"
    tmp.mkdir(exist_ok=True)
    for i, t in enumerate(times):
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf",
             f"scale=360:-2,drawtext=text='{t:.1f}s':x=10:y=10:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6",
             str(tmp / f"{i:03d}.jpg")])
    sheet = out / f"{name}.jpg"
    run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", str(tmp / "%03d.jpg"), "-vf",
         f"tile={cols}x{-(-len(times) // cols)}:padding=4", "-frames:v", "1", str(sheet)])
    keep = out / "hook-frames" if name == "hook" else None
    if keep:
        shutil.rmtree(keep, ignore_errors=True)
        tmp.rename(keep)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return sheet


def ocr(images: list[Path]) -> list[str]:
    if not shutil.which("tesseract"):
        return []
    lines: list[str] = []
    for img in images:
        r = run(["tesseract", str(img), "-", "--psm", "11"], timeout=60)
        for line in r.stdout.splitlines():
            line = line.strip()
            if len(re.sub(r"[^A-Za-z]", "", line)) >= 4 and line not in lines:
                lines.append(line)
    return lines[:12]


def transcribe(video: Path) -> dict | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None  # the AI uses transcribe_media instead
    model = WhisperModel(os.environ.get("VIRAL_WHISPER", "base"), device="cpu", compute_type="int8")
    segs, info = model.transcribe(str(video), vad_filter=True)
    segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs]
    return {"language": info.language, "segments": segs, "text": " ".join(s["text"] for s in segs),
            "speech_seconds": round(sum(s["end"] - s["start"] for s in segs), 1)}


def analyze_one(d: Path, whisper: bool) -> dict:
    video = next(iter(sorted(d.glob("video.*"))))
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    p = probe(video)
    dur = p["duration"] or 0.01
    c = cuts(video)
    shots = [b - a for a, b in zip([0.0, *c], [*c, dur]) if b - a > 0.05]
    hook_times = [0, 0.5, 1, 1.5, 2, 3]
    sheet = frames(video, d, [t for t in hook_times if t < dur], "hook", 3)
    body = [round(dur * i / 12, 2) for i in range(12)]
    contact = frames(video, d, body, "sheet", 4)
    tr = transcribe(video) if whisper and p["has_audio"] else None
    words_3s = " ".join(s["text"] for s in (tr or {}).get("segments", []) if s["start"] < 3) or None
    signals = {
        "id": d.name, "url": meta.get("url"), "platform": meta.get("platform"), "account": meta.get("account"),
        "measured": {
            "duration_s": round(dur, 1), "vertical": bool(p["height"] and p["width"] and p["height"] > p["width"]),
            "cuts": len(c), "cuts_per_10s": round(len(c) / dur * 10, 1),
            "avg_shot_s": round(statistics.mean(shots), 2) if shots else round(dur, 2),
            "first_cut_s": c[0] if c else None, "cuts_in_first_3s": sum(1 for t in c if t < 3),
            "on_screen_text_ocr": ocr(sorted((d / "hook-frames").glob("*.jpg"))),
            "speech_seconds": (tr or {}).get("speech_seconds"),
            "talking": bool(tr and tr["speech_seconds"] > dur * 0.3) if tr else None,
            "first_3s_words": words_3s, "language": (tr or {}).get("language"),
            "views": meta.get("views"), "likes": meta.get("likes"), "comments": meta.get("comments"),
            "account_median_views": meta.get("account_median_views"), "outlier": meta.get("outlier"),
            "like_rate": round(meta["likes"] / meta["views"] * 100, 2) if meta.get("likes") and meta.get("views") else None,
            "sound": meta.get("sound"), "posted": meta.get("posted"),
        },
        "judged": dict(JUDGED),
        "files": {"hook_sheet": sheet.name, "contact_sheet": contact.name},
    }
    if tr:
        (d / "transcript.json").write_text(json.dumps(tr, indent=1, ensure_ascii=False))
    (d / "signals.json").write_text(json.dumps(signals, indent=1, ensure_ascii=False))
    return signals


def cmd_analyze(a) -> None:
    for d in sorted((Path(a.dir) / "reels").glob("*/")):
        if not any(d.glob("video.*")) or ((d / "signals.json").exists() and not a.force):
            continue
        s = analyze_one(d, not a.no_transcribe)
        m = s["measured"]
        print(f"ok  {d.name}  {m['duration_s']}s  {m['cuts_per_10s']} cuts/10s  first cut {m['first_cut_s']}s"
              f"  talking={m['talking']}  text={'; '.join(m['on_screen_text_ocr'][:2])[:60]}")
    print("\nNow look at hook.jpg and sheet.jpg in each folder, read the caption and transcript, and fill in "
          "\"judged\" in every signals.json (SKILL.md, step 3).")


# ------------------------------------------------------------------ board

def load(root: Path) -> list[dict]:
    rows = []
    for d in sorted((root / "reels").glob("*/")):
        if (d / "signals.json").exists():
            s = json.loads((d / "signals.json").read_text())
            s["meta"] = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
            s["dir"] = d
            rows.append(s)
    return sorted(rows, key=lambda s: (-(s["measured"].get("outlier") or 0), -(s["measured"].get("views") or 0)))


def fmt(n) -> str:
    if not n:
        return "?"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e3 else str(n)


# The board looks like Ken's chat (web/theme.css): Geist, warm greys, white cards on a grey card, green for "beat their usual".
FONTS = Path(__file__).resolve().parents[2] / "web" / "fonts"
BOARD_PAGE = """<!doctype html><meta charset="utf-8"><title>Trend board</title><style>
@font-face{{font-family:Geist;src:url('{fonts}/geist-400.ttf');font-weight:400}}
@font-face{{font-family:Geist;src:url('{fonts}/geist-500.ttf');font-weight:500}}
@font-face{{font-family:Geist;src:url('{fonts}/geist-600.ttf');font-weight:600}}
body{{margin:0;padding:24px;width:1032px;background:#fff;color:#1c1b1a;font:15px/1.45 Geist,-apple-system,'Segoe UI',sans-serif;-webkit-font-smoothing:antialiased}}
.board{{padding:8px 8px 8px;border-radius:20px;background:#f3f2ef}}
.head{{padding:10px 12px 14px}}
h1{{margin:0;font-size:18px;font-weight:600;letter-spacing:-.01em}} .sum{{margin:2px 0 0;font-size:13px;color:#6e6d69}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
article{{display:flex;gap:14px;height:220px;padding:12px;border-radius:14px;background:#fff;box-sizing:border-box}}
img{{width:130px;height:196px;flex-shrink:0;object-fit:cover;border-radius:10px;background:#000}}
.b{{display:flex;flex-direction:column;gap:5px;min-width:0}} p{{margin:0}}
.top{{display:flex;align-items:center;justify-content:space-between;gap:8px}}
.acct{{font-size:13px;font-weight:500;color:#55544f;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.out{{padding:2px 10px;border-radius:999px;background:#edf5ef;color:#2f6b45;font-size:12px;font-weight:500;white-space:nowrap}}
.hook{{font-size:15px;font-weight:500;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.hook small{{margin-right:6px;font-size:12px;color:#6e6d69}} .m{{font-size:13px;color:#6e6d69}}
.steal{{margin-top:auto;font-size:13px;line-height:1.4;color:#2f6b45;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
</style><div class="board"><div class="head"><h1>Trend board · {niche} · {today}</h1><p class="sum">{summary}</p></div>
<div class="grid">{cards}</div></div>"""


def short_hook(kind: str | None) -> str:
    """'voice + text (spoken claim, big word captions)' → 'Spoken claim': the part a person reads at a glance."""
    if not kind:
        return "?"
    inner = re.search(r"\(([^)]+)\)", kind)
    t = (inner.group(1) if inner else kind).split(",")[0].strip()
    return t[:1].upper() + t[1:]


def cmd_board(a) -> None:
    root = Path(a.dir)
    rows = load(root)
    if not rows:
        sys.exit("No analysed reels yet: run fetch and analyze first.")
    today = datetime.now().strftime("%b %d")
    md = [f"# Trend board: {a.niche}, {today}", "", "Ranked by outlier score (views ÷ the account's usual views), "
          "then views. Numbers are public counts at fetch time.", ""]
    cards = []
    for i, s in enumerate(rows, 1):
        m, j = s["measured"], s["judged"]
        o = m.get("outlier")
        out = "100x+ their usual" if o and o >= 100 else f"{o}x their usual" if o else "no baseline"
        md += [f"## {i}. {j.get('hook_text') or s['meta'].get('title') or s['id']}",
               f"{s['url']} · @{s.get('account') or '?'} · {fmt(m.get('views'))} views · **{out}**",
               f"- Hook: {j.get('hook_type') or '?'}, {j.get('hook_why') or ''}".rstrip(", "),
               f"- Format: {j.get('format') or '?'} · {m['duration_s']}s · {m['cuts_per_10s']} cuts/10s · "
               f"text: {j.get('text_style') or '?'} · audio: {j.get('audio') or '?'}",
               f"- Why it worked: {j.get('why_it_worked') or '?'}", f"- Steal this: {j.get('steal_this') or '?'}", ""]
        thumb = s["dir"] / "hook-frames" / "001.jpg"
        cards.append(f"""<article><img src="{html.escape(os.path.relpath(thumb, root))}" alt="">
<div class="b"><div class="top"><span class="acct">{i} · @{html.escape(s.get('account') or '?')}</span><span class="out">{html.escape(out)}</span></div>
<p class="hook"><small>0–3s</small>“{html.escape(j.get('hook_text') or s['meta'].get('title') or s['id'])}”</p>
<p class="m">{html.escape(short_hook(j.get('hook_type')))} · {fmt(m.get('views'))} views · {m['duration_s']:.0f}s</p>
<p class="steal">Steal: {html.escape(j.get('steal_this') or '')}</p></div></article>""")
    fams = {"tour": "tour", "list": "list", "pov": "POV", "explain": "explainer", "reveal": "reveal",
            "challenge": "challenge", "talking head": "talking head", "montage": "montage", "before": "before/after",
            "day in": "day in my life", "nobody tells": "things nobody tells you"}
    fmts: dict[str, int] = {}
    for s in rows:
        f = (s["judged"].get("format") or "").lower()
        for k, name in fams.items():
            if k in f:
                fmts[name] = fmts.get(name, 0) + 1
    voice = sum(1 for s in rows if "voice" in (s["judged"].get("hook_type") or ""))
    pace = statistics.median(s["measured"]["cuts_per_10s"] for s in rows)
    length = statistics.median(s["measured"]["duration_s"] for s in rows)
    summary = (f"{len(rows)} reels · median {length:.0f}s · {pace:.1f} cuts per 10s · {voice} of {len(rows)} hook with "
               "their voice · formats: " + ", ".join(f"{k} ({v})" for k, v in sorted(fmts.items(), key=lambda x: -x[1])[:4]))
    md[3:3] = [f"**This week:** {summary}", ""]
    (root / "board.md").write_text("\n".join(md))
    (root / "board.html").write_text(BOARD_PAGE.format(fonts=FONTS.as_uri() if FONTS.is_dir() else "", niche=html.escape(a.niche),
                                                        today=today, summary=html.escape(summary), cards="".join(cards)))
    png = shot(root / "board.html", root / "board.png", rows)
    print(f"{root / 'board.md'}\n{root / 'board.html'}" + (f"\n{png}" if png else "\n(no Chrome/Chromium for board.png)"))


def shot(page: Path, png: Path, rows: list) -> Path | None:
    """board.png from board.html, so the board shows as a picture in the chat and on their phone."""
    for b in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
        if shutil.which(b):
            h = 112 + 232 * -(-len(rows) // 2)
            run([b, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2", f"--window-size=1080,{h}",
                 f"--screenshot={png}", page.resolve().as_uri()], timeout=120)
            return png if png.exists() else None
    return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("dir"); f.add_argument("--niche", default="travel"); f.add_argument("--tags"); f.add_argument("--limit", type=int, default=30)
    f.add_argument("--accounts", help="comma separated accounts they look up to (YouTube @handles or channel URLs)")
    f.add_argument("--per-account", type=int, default=3)
    g = sub.add_parser("fetch")
    g.add_argument("dir"); g.add_argument("urls", nargs="+"); g.add_argument("--no-baseline", action="store_true")
    n = sub.add_parser("analyze")
    n.add_argument("dir"); n.add_argument("--no-transcribe", action="store_true"); n.add_argument("--force", action="store_true")
    b = sub.add_parser("board")
    b.add_argument("dir"); b.add_argument("--niche", default="travel")
    a = p.parse_args()
    {"find": cmd_find, "fetch": cmd_fetch, "analyze": cmd_analyze, "board": cmd_board}[a.cmd](a)


if __name__ == "__main__":
    main()
