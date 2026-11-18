// What a render is made of, worked out without drawing anything (no Remotion here, so tests can load it).
// Everything is named by what's in it, so after a change only what changed is drawn or encoded again:
// - the code each layer runs (captions don't depend on a graphic Ken wrote; one graphic doesn't depend on another),
// - the reel's final picture, in pieces of a few seconds, each named by everything that shows in it.
import { execFile, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { availableParallelism } from "node:os";
import { dirname, join, normalize } from "node:path";

export const FPS = 30;
export const W = 1080, H = 1920;
export const SHEET_LIFT = 340; // as in src/theme.ts
export const PIECE = 90; // frames (3 s) per piece of the final picture

export const hash = (value) => createHash("sha256").update(typeof value === "string" ? value : JSON.stringify(value)).digest("hex");

const codeFiles = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? codeFiles(join(dir, e.name)) : [join(dir, e.name)]));
const contents = (files) => files.sort().map((f) => [f, readFileSync(f, "utf8")]);

// The project's code (custom graphics included).
export const codeHash = (src = "src") => hash(contents(codeFiles(src)));

// src/graphics holds the graphics (Ken's own among them); everything else, and layout.tsx (the captions use it), is
// what every layer runs.
const graphicsDir = (src) => join(src, "graphics");
const isGraphic = (src, f) => f.startsWith(graphicsDir(src) + "/") && !f.endsWith("/layout.tsx");
export const baseHash = (src = "src") => hash(contents(codeFiles(src).filter((f) => !isGraphic(src, f))));

const IMPORT = /(?:import|export)[^"']*?from\s*["'](\.{1,2}\/[^"']+)["']|import\s*["'](\.{1,2}\/[^"']+)["']/g;
const resolveImport = (from, spec) => {
  const base = normalize(join(dirname(from), spec));
  return [base, `${base}.tsx`, `${base}.ts`, join(base, "index.tsx"), join(base, "index.ts")].find((f) => existsSync(f) && statSync(f).isFile());
};

// The code a graphic of each of these types runs: the shared code, plus its component's file in src/graphics and the
// graphics files that file imports. When a type can't be traced, every graphics file counts (always safe).
export function graphicsHash(types, src = "src") {
  const all = codeFiles(src).filter((f) => isGraphic(src, f));
  let files = null;
  try {
    const index = readFileSync(join(graphicsDir(src), "index.ts"), "utf8");
    const imported = {};
    for (const m of index.matchAll(/import\s*\{([^}]*)\}\s*from\s*["'](\.{1,2}\/[^"']+)["']/g)) {
      for (const name of m[1].split(",").map((n) => n.trim().replace(/^type\s+/, "").split(/\s+as\s+/).pop()).filter(Boolean)) imported[name] = m[2];
    }
    const registered = Object.fromEntries([...index.matchAll(/(\w+)\s*:\s*\{\s*component\s*:\s*(\w+)/g)].map((m) => [m[1], m[2]]));
    const todo = [...new Set(types)].map((t) => imported[registered[t]] && resolveImport(join(graphicsDir(src), "index.ts"), imported[registered[t]]));
    if (todo.every(Boolean)) {
      const seen = new Set();
      while (todo.length) {
        const f = todo.pop();
        if (seen.has(f) || !isGraphic(src, f)) continue;
        seen.add(f);
        if (!/\.(tsx?|jsx?|mjs)$/.test(f)) continue;
        for (const m of readFileSync(f, "utf8").matchAll(IMPORT)) {
          const dep = resolveImport(f, m[1] ?? m[2]);
          if (dep) todo.push(dep);
        }
      }
      files = [...seen];
    }
  } catch {}
  return hash([baseHash(src), contents(files ?? all)]);
}

// Captions: what one look is. Only what's drawn counts (each word, whether it's emphasized, how far in it is), not when
// it's said, so a look reused after a sentence is dropped or the timing moves isn't drawn again.
export const captionKey = (code, style, tokens, shown) => hash([code, style, tokens, shown]).slice(0, 24);

// Where the picture is cut into pieces: at every jump cut, then every 3 s, as render.mjs does. A piece is named by
// what's in it, timed from its own start, so a piece whose content only moved keeps its name.
export function pieceEdges(total, props, piece = PIECE) {
  const starts = new Set([0]);
  for (const c of [...(props.cuts ?? []), ...(props.framing ?? []).map((f) => f.at)]) starts.add(Math.round(c * FPS));
  const bounds = [...starts].filter((f) => f > 0 && f < total).sort((x, y) => x - y);
  const edges = [0];
  for (const next of [...bounds, total]) {
    for (let f = edges[edges.length - 1] + piece; f < next - piece / 3; f += piece) edges.push(f);
    edges.push(next);
  }
  return edges;
}

// The footage under frames [a, b): the source ranges the cut kept there (so cutting again with the same words, or a
// different ending, gives the same name), or the video file itself when it isn't the cut's (Ken made it by hand).
export function footage(work, props, a, b, fileHash) {
  const path = join(work, props.video);
  let cutlist = null;
  try {
    cutlist = JSON.parse(readFileSync(join(work, "cutlist.json"), "utf8"));
  } catch {}
  const st = existsSync(path) ? statSync(path) : null;
  const ours = cutlist?.pieces && cutlist.source && cutlist.video && cutlist.video.name === props.video && st &&
    cutlist.video.size === st.size && Math.abs(cutlist.video.mtime - st.mtimeMs / 1000) < 0.01;
  if (!ours) return ["file", fileHash(), a, b];
  const speed = cutlist.speed ?? 1;
  const t0 = a / FPS, t1 = b / FPS;
  const under = [];
  let pos = 0;
  for (const [s, e] of cutlist.pieces) {
    const length = (e - s) / speed;
    const from = Math.max(t0, pos), to = Math.min(t1, pos + length);
    if (from < to) under.push([+(s + (from - pos) * speed).toFixed(3), +(s + (to - pos) * speed).toFixed(3)]);
    pos += length;
  }
  // Frames past the end of the cut hold its last frame; how many is part of what the piece shows.
  const end = Math.round(pos * FPS);
  return ["cut", cutlist.version ?? 0, cutlist.source, speed, under, Math.max(0, b - Math.max(a, end)), a >= end];
}

const E = (x) => `(1-pow(1-clip(${x},0,1),3))`;
const r4 = (x) => +x.toFixed(4);
// The first frame n where n/FPS >= t - eps, decided exactly as the old one-pass expression of time decided it.
const frameAt = (t, eps = 1e-6) => {
  let n = Math.max(0, Math.ceil((t - eps) * FPS));
  while (n > 0 && (n - 1) / FPS >= t - eps) n--;
  while (n / FPS < t - eps) n++;
  return n;
};

// The footage's motion over frames [a, b) as one ffmpeg perspective filter, in the piece's own time (frame 0 is a):
// each part's framing, the push-ins, and the lift while paper is up. Only what happens in the piece is in it.
// @F and @T stand for the frame number and the time, which the filter and its enable option call differently.
export function perspective(props, graphics, a, b) {
  const style = props.style ?? {};
  const t0 = a / FPS, t1 = b / FPS;
  let scale = "1";
  if (style.zoomCuts ?? true) {
    if (props.framing) {
      const at = props.framing.map((f) => ({ n: frameAt(f.at), scale: f.scale }));
      scale = String(at.filter((f) => f.n <= a).pop()?.scale ?? 1);
      for (const f of at.filter((f) => f.n > a && f.n < b)) scale = `if(gte(@F,${f.n - a}),${f.scale},${scale})`;
    } else {
      // Older props: the first jump cut punches in, the ones after it go back to normal.
      const at = (props.cuts ?? []).map((c, i) => ({ n: frameAt(c, 0), scale: i === 0 ? 1.06 : 1 }));
      scale = String(at.filter((f) => f.n <= a).pop()?.scale ?? 1);
      for (const f of at.filter((f) => f.n > a && f.n < b)) scale = `if(gte(@F,${f.n - a}),${f.scale},${scale})`;
    }
  }
  for (const z of (props.zooms ?? []).filter((z) => z.start < t1 && z.end > t0)) {
    const s = r4(z.start - t0), e = r4(z.end - t0);
    scale = `(${scale})*if(between(@T,${s},${e}),1+0.14*min(${E(`(@T-${s})/1.2`)},${E(`(${e}-@T)/0.25`)}),1)`;
  }
  let lift = "0";
  for (const g of graphics.filter((g) => g.placement === "sheet" && g.start < t1 && g.end > t0)) {
    const s = r4(g.start - t0), e = r4(g.end - t0);
    lift = `max(${lift},if(between(@T,${s},${e}),min(${E(`(@T-${s})/0.4`)},${E(`(${e}-@T)/0.3`)}),0))`;
  }
  if (scale === "1" && lift === "0") return "null"; // nothing moves in this piece
  lift = `(${SHEET_LIFT}*${lift})`;
  // Each corner of the frame shows this point of the source: centre + (corner - centre + lift) / scale.
  const x = (side) => `${W / 2}${side}${W / 2}/(${scale})`;
  const y = (side) => `${H / 2}+(${lift}${side}${H / 2})/(${scale})`;
  // It's skipped (the frame passes through untouched) while the framing is 100% and nothing is lifting.
  const moved = `gt(abs((${scale})-1)+${lift},0.0001)`.replaceAll("@F", "n").replaceAll("@T", "t");
  const filter = `perspective=x0='${x("-")}':y0='${y("-")}':x1='${x("+")}':y1='${y("-")}':x2='${x("-")}':y2='${y("+")}':x3='${x("+")}':y3='${y("+")}':interpolation=linear:eval=frame`;
  return `${filter.replaceAll("@F", "in").replaceAll("@T", `(in/${FPS})`)}:enable='${moved}'`;
}

// ---- The finished picture in pieces.
const ENCODE = ["-c:v", "libx264", "-crf", "19", "-maxrate", "5500k", "-bufsize", "11000k", "-preset", "veryfast"];

// Each piece of frames [a, b): its caption looks (runs of {name, frames}, "blank" for none), the graphics clips on it
// ({clip, at}: at is where the clip starts, in frames from the piece's start), its motion, and its name.
// `looks` is per frame null or {key}; `clips` are {a, b, key, file}.
export function planPieces({ work, props, graphics, looks, clips, total, footageHash, dir, piece = PIECE }) {
  const edges = pieceEdges(total, props, piece);
  const pieces = [];
  for (let i = 0; i + 1 < edges.length; i++) {
    const a = edges[i], b = edges[i + 1];
    const runs = [];
    for (let f = a; f < b; f++) {
      const name = looks[f] ? looks[f].key : "blank";
      if (runs.length && runs[runs.length - 1].name === name) runs[runs.length - 1].frames++;
      else runs.push({ name, frames: 1 });
    }
    const on = clips.filter((c) => c.a < b && c.b > a).map((c) => ({ clip: c, at: c.a - a }));
    const motion = perspective(props, graphics, a, b);
    const key = hash(["1", ENCODE, b - a, footage(work, props, a, b, footageHash), motion, runs.map((r) => [r.name, r.frames]),
      on.map((o) => [o.clip.key, o.at]), props.style?.grain === true]).slice(0, 24);
    pieces.push({ a, b, key, file: join(dir, `${key}.mp4`), runs, on, motion });
  }
  return pieces;
}

export const probeJson = (file, entries) => JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_entries", entries, "-of", "json", file]).toString());
// A seek half a frame early lands exactly on the frame wanted (the one before it is dropped), whatever the rounding.
const seek = (frame) => (frame > 0 ? ["-ss", ((frame - 0.5) / FPS).toFixed(6)] : []);

// Encodes pieces, a few at a time (ffmpeg's filters mostly use one core each): the footage with its motion, the
// graphics, the captions and the grain, in the piece's own time. Past the end of the footage its last frame holds.
export async function encodePieces(pieces, { video, captionFile, vignette, grain, jobs }) {
  const info = probeJson(video, "stream=codec_type,nb_frames,duration").streams.find((x) => x.codec_type === "video");
  const videoFrames = Math.max(1, Number(info?.nb_frames) || Math.round(Number(info?.duration) * FPS));
  const encode = (p) => {
    const n = p.b - p.a;
    const base = p.file.replace(/\.mp4$/, ".part");
    // The captions: each look held until the next one, each image read at 30 fps (a still's default is 25).
    const entry = (name) => `file '${captionFile(name)}'\noption framerate ${FPS}`;
    writeFileSync(`${base}.txt`, "ffconcat version 1.0\n" + p.runs.map((r) => `${entry(r.name)}\nduration ${(r.frames / FPS).toFixed(6)}`).join("\n") + `\n${entry(p.runs[p.runs.length - 1].name)}\n`);
    const inputs = [...seek(Math.min(p.a, videoFrames - 1)), "-i", video, "-f", "concat", "-safe", "0", "-i", `${base}.txt`];
    // VP9 keeps its transparency only through libvpx's own decoder.
    p.on.forEach(({ clip, at }) => inputs.push(...(clip.file.endsWith(".webm") ? ["-c:v", "libvpx-vp9"] : []), ...seek(-at), "-i", clip.file));
    const vf = [`[0:v]setpts=PTS-STARTPTS,fps=${FPS}:start_time=0,tpad=stop_mode=clone:stop=${n},${p.motion}[v0]`];
    let v = "v0";
    p.on.forEach(({ at }, i) => {
      vf.push(`[${i + 2}:v]setpts=PTS-STARTPTS+${Math.max(0, at)}/(${FPS}*TB)[o${i}]`, `[${v}][o${i}]overlay=eof_action=pass:format=auto[g${i}]`);
      v = `g${i}`;
    });
    vf.push(`[1:v]fps=${FPS},format=rgba[cap]`, `[${v}][cap]overlay=eof_action=pass:format=auto:shortest=0[c]`);
    if (grain) {
      inputs.push("-loop", "1", "-i", vignette);
      vf.push(`[c][${2 + p.on.length}:v]overlay=shortest=1:format=auto,noise=c0s=4:c0f=t+u,format=yuv420p[v]`);
    } else vf.push(`[c]format=yuv420p[v]`);
    writeFileSync(`${base}.graph`, vf.join(";\n"));
    return new Promise((done, fail) =>
      execFile("ffmpeg", ["-v", "error", "-y", ...inputs, "-filter_complex_script", `${base}.graph`, "-map", "[v]", "-frames:v", String(n), "-an",
        ...ENCODE, `${base}.mp4`], { maxBuffer: 1 << 24 }, (err, _out, stderr) => {
        rmSync(`${base}.txt`, { force: true });
        rmSync(`${base}.graph`, { force: true });
        const frames = err ? 0 : Number(probeJson(`${base}.mp4`, "stream=nb_frames").streams[0]?.nb_frames);
        if (frames !== n) {
          rmSync(`${base}.mp4`, { force: true });
          return fail(new Error(`piece at ${(p.a / FPS).toFixed(1)}s: ${err ? stderr.slice(-1500) : `${frames} frames, expected ${n}`}`));
        }
        renameSync(`${base}.mp4`, p.file);
        done();
      }));
  };
  const queue = [...pieces];
  await Promise.all(Array.from({ length: jobs ?? Math.max(1, Math.min(4, Math.floor(availableParallelism() / 2))) }, async () => {
    while (queue.length) await encode(queue.shift());
  }));
}

// The reel: the pieces joined as they are, and the whole sound under them, made fresh every time (a second or two), so
// a join can't click or drop out: the footage's own (held in silence past its end) and the sound effects ({file, at}).
// Capped near what Instagram keeps anyway (it re-encodes everything), so a one-minute reel is about 45 MB: small
// enough for a Telegram bot to send as the file itself.
export function joinPieces(pieces, { video, frames, out, list, sfx = [] }) {
  writeFileSync(list, "ffconcat version 1.0\n" + pieces.map((p) => `file '${p.file}'`).join("\n") + "\n");
  const seconds = (frames / FPS).toFixed(6);
  const af = [`[1:a]apad=whole_dur=${seconds},atrim=end=${seconds}${sfx.length ? "[voice]" : "[a]"}`];
  if (sfx.length) {
    af.push(...sfx.map((s, i) => `[${i + 2}:a]adelay=${Math.round(s.at * 1000)}:all=1[s${i}]`),
      `[voice]${sfx.map((_, i) => `[s${i}]`).join("")}amix=inputs=${sfx.length + 1}:normalize=0:duration=first[a]`);
  }
  execFileSync("ffmpeg", [
    "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", list, "-vn", "-i", video, ...sfx.flatMap((s) => ["-i", s.file]),
    "-filter_complex", af.join(";"), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-movflags", "+faststart", out,
  ], { stdio: "inherit" });
  rmSync(list, { force: true });
}
