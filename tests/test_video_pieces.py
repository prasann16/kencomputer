"""A revision only redraws and re-encodes what changed: the kit's plan.mjs (run with node) names every layer and every
piece of the finished picture by what's in it, and pieces join back into a reel with no seam."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLAN = Path(__file__).resolve().parent.parent / "video" / "kit" / "template" / "plan.mjs"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


def node(cwd: Path, script: str):
    """Runs `script` (an ES module with plan.mjs imported as P) in `cwd` and returns what it prints, as JSON."""
    (cwd / "t.mjs").write_text(f"import * as P from {json.dumps(PLAN.as_uri())};\n{script}")
    r = subprocess.run(["node", "t.mjs"], cwd=cwd, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def write(root: Path, files: dict):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


SRC = {
    "src/Reel.tsx": 'import { GRAPHICS } from "./graphics";\nimport { accentType } from "./graphics/layout";\n',
    "src/theme.ts": "export const x = 1;\n",
    "src/graphics/layout.tsx": 'import { x } from "../theme";\n',
    "src/graphics/index.ts": 'import { Cta } from "./cta";\nimport { Design } from "./design";\n'
                             'export const GRAPHICS = {\n  design: { component: Design, placement: "over" },\n'
                             '  cta: { component: Cta, placement: "over" },\n};\n',
    "src/graphics/design.tsx": 'import { x } from "../theme";\nexport const Design = () => null;\n',
    "src/graphics/cta.tsx": 'import { Arrow } from "./arrow";\nexport const Cta = () => null;\n',
    "src/graphics/arrow.tsx": "export const Arrow = () => null;\n",
}
HASHES = 'console.log(JSON.stringify({base: P.baseHash(), design: P.graphicsHash(["design"]), cta: P.graphicsHash(["cta"]), odd: P.graphicsHash(["mystery"])}));'


@needs_node
def test_a_new_graphic_redraws_only_itself(tmp_path):
    write(tmp_path, SRC)
    before = node(tmp_path, HASHES)
    # Ken changes his CTA graphic (or something only it imports): captions and the other graphics are kept.
    write(tmp_path, {"src/graphics/arrow.tsx": "export const Arrow = () => 1;\n"})
    after = node(tmp_path, HASHES)
    assert after["base"] == before["base"] and after["design"] == before["design"]
    assert after["cta"] != before["cta"]
    assert after["odd"] != before["odd"]  # a graphic that can't be traced counts every graphics file
    # Registering one more graphic changes nothing drawn.
    write(tmp_path, {"src/graphics/index.ts": SRC["src/graphics/index.ts"].replace("};", '  more: { component: Design, placement: "over" },\n};')})
    assert node(tmp_path, HASHES)["design"] == after["design"]
    # Shared code (the theme, the captions' layout) redraws everything.
    write(tmp_path, {"src/graphics/layout.tsx": "// changed\n"})
    shared = node(tmp_path, HASHES)
    assert shared["base"] != after["base"] and shared["design"] != after["design"] and shared["cta"] != after["cta"]


@needs_node
def test_pieces_start_at_cuts_and_moved_content_keeps_its_name(tmp_path):
    out = node(tmp_path, """
const props = { cuts: [100 / 30], framing: [{ at: 0, scale: 1 }, { at: 100 / 30, scale: 1.06 }], zooms: [{ start: 5, end: 6.5 }] };
const moved = { cuts: [130 / 30], framing: [{ at: 0, scale: 1 }, { at: 130 / 30, scale: 1.06 }], zooms: [{ start: 6, end: 7.5 }] };
console.log(JSON.stringify({
  edges: P.pieceEdges(400, props),
  // The same push-in, a second later in the reel, in a piece a second later: the same filter, so the same piece.
  same: P.perspective(props, [], 100, 190) === P.perspective(moved, [], 130, 220),
  still: P.perspective({ framing: [{ at: 0, scale: 1 }] }, [], 0, 90),
  sheet: P.perspective({}, [{ placement: "sheet", start: 1, end: 2 }], 0, 90).includes("340"),
}));""")
    assert out["edges"] == [0, 100, 190, 280, 400]
    assert out["same"] and out["still"] == "null" and out["sheet"]


@needs_node
def test_the_footage_is_named_by_what_the_cut_kept(tmp_path):
    video = tmp_path / "cut.mp4"
    video.write_bytes(b"x" * 100)

    def cutlist(pieces):
        st = video.stat()
        (tmp_path / "cutlist.json").write_text(json.dumps({"pieces": pieces, "speed": 1, "version": 1, "source": {"name": "source.mp4"},
                                                           "video": {"name": "cut.mp4", "size": st.st_size, "mtime": round(st.st_mtime, 3)}}))

    script = 'const f = (a, b) => JSON.stringify(P.footage(".", { video: "cut.mp4" }, a, b, () => "whole-file"));\n' \
             'console.log(JSON.stringify([f(0, 90), f(270, 360), f(450, 540)]));'
    cutlist([[0, 10], [20, 30]])
    first = node(tmp_path, script)
    cutlist([[0, 10], [20, 25]])  # a shorter ending
    shorter = node(tmp_path, script)
    assert shorter[0] == first[0] and shorter[1] == first[1] and shorter[2] != first[2]
    assert '"cut"' in first[0]
    video.write_bytes(b"y" * 120)  # changed by hand: named by its bytes instead
    assert '"whole-file"' in node(tmp_path, script)[0]


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@needs_node
@needs_ffmpeg
def test_pieces_join_into_the_same_reel_without_a_seam(tmp_path):
    # 4 s of moving picture (every frame differs), sound 3.5 s long; the reel is 140 frames, so the last 20 hold.
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=4", "-f", "lavfi", "-i", "sine=f=440:d=3.5", "-c:v", "libx264",
           "-preset", "ultrafast", "-g", "250", "-pix_fmt", "yuv420p", "-c:a", "aac", str(tmp_path / "cut.mp4"))
    ffmpeg("-f", "lavfi", "-i", "color=c=black@0.0:s=1080x1920,format=rgba", "-frames:v", "1", str(tmp_path / "blank.png"))
    ffmpeg("-f", "lavfi", "-i", "color=c=red@0.6:s=1080x1920,format=rgba", "-frames:v", "1", str(tmp_path / "look.png"))
    # A see-through graphic that changes every frame, across a join (frames 40 to 100).
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=1080x1920:r=30:d=2,format=yuva444p10le,colorchannelmixer=aa=0.5",
           "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(tmp_path / "clip.mov"))
    (tmp_path / "pieces").mkdir()
    (tmp_path / "whole").mkdir()
    out = node(tmp_path, """
import { execFileSync } from "node:child_process";
const props = { video: "cut.mp4", framing: [{ at: 0, scale: 1 }, { at: 1.5, scale: 1.06 }], zooms: [{ start: 0.8, end: 2.6 }] };
const looks = Array.from({ length: 140 }, (_, f) => (f >= 50 && f < 70 ? { key: "look" } : null));
const clips = [{ a: 40, b: 100, key: "clip", file: "clip.mov" }];
const plan = (dir, piece, l = looks) => P.planPieces({ work: ".", props, graphics: [], looks: l, clips, total: 140, footageHash: () => "h", dir, piece });
const opts = { video: "cut.mp4", captionFile: (n) => `${process.cwd()}/${n}.png`, grain: false, jobs: 2 };
const pieces = plan("pieces", 40), whole = plan("whole", 100000);
await P.encodePieces(pieces, opts);
await P.encodePieces(whole, opts);
P.joinPieces(pieces, { video: "cut.mp4", frames: 140, out: "a.mp4", list: "a.txt" });
P.joinPieces(whole, { video: "cut.mp4", frames: 140, out: "b.mp4", list: "b.txt" });
// Per-frame difference between the two (luma PSNR), each frame's time, and the sound's length.
const psnr = execFileSync("ffmpeg", ["-v", "error", "-i", "a.mp4", "-i", "b.mp4", "-lavfi", "[0:v][1:v]psnr=stats_file=-", "-f", "null", "-"]).toString()
  .trim().split("\\n").map((l) => Number(l.match(/psnr_y:(\\S+)/)[1].replace("inf", "99")));
const times = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-select_streams", "v", "-show_entries", "frame=pts_time", "-of", "json", "a.mp4"]))
  .frames.map((f) => Number(f.pts_time));
const streams = P.probeJson("a.mp4", "stream=codec_type,duration,nb_frames").streams;
// A change to the captions late in the reel renames only the pieces it's on.
const later = plan("pieces", 40, looks.map((l, f) => (f >= 125 ? { key: "other" } : l)));
console.log(JSON.stringify({ edges: pieces.map((p) => p.a), psnr, times, streams,
  changed: later.filter((p, i) => p.key !== pieces[i].key).map((p) => p.a) }));
""")
    assert out["edges"] == [0, 45, 85, 125]  # a piece starts at the jump cut (1.5 s)
    assert len(out["psnr"]) == 140 and min(out["psnr"]) > 35, out["psnr"]
    steps = [b - a for a, b in zip(out["times"], out["times"][1:])]
    assert len(out["times"]) == 140 and all(abs(s - 1 / 30) < 1e-3 for s in steps)
    video = next(s for s in out["streams"] if s["codec_type"] == "video")
    audio = next(s for s in out["streams"] if s["codec_type"] == "audio")
    assert int(video["nb_frames"]) == 140 and abs(float(audio["duration"]) - 140 / 30) < 0.05
    assert out["changed"] == [out["edges"][-1]]
