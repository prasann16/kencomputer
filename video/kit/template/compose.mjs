// Renders the reel the fast way: Chrome draws only what has to look designed (each distinct look of the captions,
// once, and the graphics for the seconds they're on screen, on a transparent background), and ffmpeg does the
// footage (framing, push-ins, the lift under paper, grain) and lays everything over it in one pass.
// Both kinds of drawing are kept by what's in them, and so is the finished picture, in pieces of a few seconds: after a
// change only what changed is drawn and encoded again, and the reel is joined from its pieces without re-encoding.
// Usage (from this folder): node compose.mjs <work_dir> <out.mp4> [--plan]
// --plan only says how much would be drawn, without drawing anything.
// Whip pans (chapters) aren't done here; render.mjs still does everything in Chrome if a reel needs them.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";

import { createTikTokStyleCaptions } from "@remotion/captions";
import { openBrowser, renderFrames, renderMedia, selectComposition } from "@remotion/renderer";

import { cachedBundle, oneAtATime, placements } from "./lib.mjs";
import { baseHash, captionKey, encodePieces, FPS, graphicsHash, H, hash, joinPieces, planPieces, probeJson, readCutlist, steadyFootage, W } from "./plan.mjs";

const args = process.argv.slice(2);
const plan = args.includes("--plan");
const [work, out] = args.filter((a) => !a.startsWith("--")).map((p) => p && resolve(p));
if (!work || !out) {
  console.error("Usage: node compose.mjs <work_dir> <out.mp4> [--plan]");
  process.exit(2);
}
const props = JSON.parse(readFileSync(join(work, "props.json"), "utf8"));
const style = props.style ?? {};
const total = Math.max(1, Math.round(props.durationSeconds * FPS));
const concurrency = (() => {
  const c = process.env.KEN_RENDER_CONCURRENCY;
  return !c ? null : /^\d+$/.test(c) ? Number(c) : c;
})();
const layers = join(work, ".layers");
mkdirSync(join(layers, "captions"), { recursive: true });
mkdirSync(join(layers, "graphics"), { recursive: true });
mkdirSync(join(layers, "pieces"), { recursive: true });
const code = baseHash();

const ease = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - (1 - x) ** 3);
const PLACEMENTS = placements();
const graphics = (props.graphics ?? []).map((g) => ({ ...g, placement: g.placement ?? PLACEMENTS[g.type] ?? "over" }));

// ---- Captions: what they look like at every frame, exactly as Reel.tsx's Captions draws them.
const words = props.words ?? [];
const emphasized = new Set(words.filter((w) => w.emph).map((w) => Math.round(w.start * 1000)));
const pages = createTikTokStyleCaptions({
  captions: words.map((w) => ({ text: ` ${w.text.trim()}`, startMs: w.start * 1000, endMs: w.end * 1000, timestampMs: null, confidence: null })),
  combineTokensWithinMilliseconds: style.wordsPerPageMs ?? 1100,
}).pages;
const hidden = graphics.filter((g) => g.hideCaptions ?? g.placement !== "over").map((g) => [g.start, g.end]);
const looks = new Array(total).fill(null); // per frame: null (no captions) or {key, frame}
if (style.captions !== false) {
  pages.forEach((page, i) => {
    const nextStart = pages[i + 1]?.startMs ?? page.startMs + page.durationMs;
    let end = Math.min(nextStart, page.startMs + page.durationMs + 400) / 1000;
    let start = page.startMs / 1000;
    for (const [a, b] of hidden) if (start >= a && start < b) start = b;
    for (const [a] of hidden) if (a > start && a < end) end = a;
    if (end - start < 0.15) return;
    const from = Math.round(start * FPS);
    const duration = Math.max(1, Math.round((end - start) * FPS));
    const tokens = page.tokens.map((t) => [t.text, emphasized.has(Math.round(t.fromMs))]);
    for (let f = from; f < Math.min(total, from + duration); f++) {
      const nowMs = start * 1000 + ((f - from) / FPS) * 1000;
      const shown = page.tokens.map((t) => +ease((nowMs - t.fromMs) / 120).toFixed(3));
      looks[f] = { key: captionKey(code, style, tokens, shown), frame: f };
    }
  });
}
const firstOf = new Map();
for (const l of looks) if (l && !firstOf.has(l.key)) firstOf.set(l.key, l.frame);
const capFile = (key) => join(layers, "captions", `${key}.png`);

// ---- Graphics: one transparent clip per stretch where something is on screen (graphics and the hook).
const hookSeconds = props.hookSeconds ?? props.titleSeconds ?? 2.8;
const hook = props.hook ?? props.title;
const spans = graphics.map((g) => [Math.round(g.start * FPS), Math.round(g.start * FPS) + Math.max(1, Math.round((g.end - g.start) * FPS))]);
if (hook) spans.push([0, Math.round(hookSeconds * FPS)]);
spans.sort((x, y) => x[0] - y[0]);
const stretches = [];
for (const [a, b] of spans) {
  const last = stretches[stretches.length - 1];
  if (last && a <= last[1] + FPS / 2) last[1] = Math.max(last[1], b);
  else stretches.push([a, b]);
}
const clips = stretches.map(([a, b]) => {
  b = Math.min(b, total);
  const t0 = a / FPS, t1 = b / FPS;
  const rel = (t) => Math.round(t * FPS) - a;
  const on = graphics.filter((g) => g.start < t1 && g.end > t0);
  const key = hash([
    graphicsHash(on.map((g) => g.type)), style, b - a,
    on.map((g) => ({ ...g, start: rel(g.start), end: rel(g.end) })),
    hook && hookSeconds > t0 ? [hook, rel(hookSeconds)] : null,
  ]).slice(0, 24);
  const file = join(layers, "graphics", `${key}.mov`);
  const small = join(layers, "graphics", `${key}.webm`);
  return { a, b, key, file: existsSync(file) || !existsSync(small) ? file : small };
});

// Ken's cloud computers start fresh on every job, so what was drawn for a reel before lives in a store instead
// (KEN_LAYER_STORE: a URL that takes GET and PUT of <name>): fetched before drawing, filled after. Graphics go
// there as small VP9 clips with transparency (a few hundred KB, where the ProRes drawn here is hundreds of MB).
// On a Mac there's no store and the folder keeps everything.
const store = process.env.KEN_LAYER_STORE;
const inBatches = async (items, fn) => {
  for (let i = 0; i < items.length; i += 16) await Promise.all(items.slice(i, i + 16).map(fn));
};
// A store that's slow or down only means drawing again, never a render that waits: one failed call and it's
// left alone for the rest of this render.
let storeDown = false;
const storeCall = async (name, init, timeout) => {
  if (storeDown) return null;
  try {
    return await fetch(`${store}/${name}`, { ...init, signal: AbortSignal.timeout(timeout) });
  } catch (e) {
    storeDown = true;
    console.log(`layer store unreachable (${e.name}), drawing everything here`);
    return null;
  }
};
const fetchLayer = async (file) => {
  const res = await storeCall(basename(file), {}, 15_000);
  if (res?.ok) writeFileSync(file, Buffer.from(await res.arrayBuffer()));
  else await res?.body?.cancel();
};
const saveLayer = async (file) => {
  const res = await storeCall(basename(file), { method: "PUT", body: readFileSync(file) }, 60_000);
  await res?.body?.cancel();
};
if (store) {
  const asked = Date.now();
  await inBatches([...firstOf.keys()].filter((key) => !existsSync(capFile(key))).map(capFile), fetchLayer);
  await inBatches(clips.filter((c) => !existsSync(c.file)), async (c) => {
    c.file = join(layers, "graphics", `${c.key}.webm`);
    await fetchLayer(c.file);
    if (!existsSync(c.file)) c.file = join(layers, "graphics", `${c.key}.mov`);
  });
  console.log(`checked the layer store (${Math.round((Date.now() - asked) / 1000)}s)`);
}
const capTodo = [...firstOf].filter(([key]) => !existsSync(capFile(key)));
const clipTodo = clips.filter((c) => !existsSync(c.file));

// ---- The picture in pieces, each named by everything that shows in it (see plan.mjs).
const videoPath = join(work, props.video);
let fileHash;
const footageHash = () => (fileHash ??= hash(readFileSync(videoPath)));
const pieceDir = join(layers, "pieces");
const pieces = planPieces({ work, props, graphics, looks, clips, total, footageHash, dir: pieceDir, steady: steadyFootage(videoPath, readCutlist(work)) });
const pieceTodo = pieces.filter((p) => !existsSync(p.file));

console.log(`captions: ${firstOf.size} looks, ${capTodo.length} to draw; graphics: ${clips.length} clips (${clips.reduce((n, c) => n + c.b - c.a, 0)} frames), ${clipTodo.length} to draw; picture: ${pieces.length} pieces, ${pieceTodo.length} to encode (${pieceTodo.reduce((n, p) => n + p.b - p.a, 0)} of ${total} frames)`);
if (plan) process.exit(0);

// ---- Draw what's missing.
const started = Date.now();
// A line every tenth of the way, so a slow draw shows it's moving (and a stuck one shows where).
const progress = (what, total) => {
  let shown = 0;
  return (done) => {
    const tenth = Math.floor((done / total) * 10);
    if (tenth > shown && tenth < 10) {
      shown = tenth;
      console.log(`${what}: ${done}/${total} (${Math.round((Date.now() - started) / 1000)}s)`);
    }
  };
};
if (capTodo.length || clipTodo.length) {
  await oneAtATime("compose");
  const step = (what) => console.log(`${what} (${Math.round((Date.now() - started) / 1000)}s)`);
  const serveUrl = await cachedBundle(work, props, { video: false });
  step("bundle ready");
  const browser = await openBrowser("chrome");
  step("Chrome open");
  try {
    if (capTodo.length) {
      const inputProps = { ...props, pick: capTodo.map(([, f]) => f) };
      const composition = await selectComposition({ serveUrl, id: "CaptionsLayer", inputProps, puppeteerInstance: browser });
      const dir = join(layers, "captions", `.draw-${process.pid}`);
      await renderFrames({
        serveUrl, composition, inputProps, puppeteerInstance: browser, imageFormat: "png", outputDir: dir,
        frameRange: [0, capTodo.length - 1], onStart: () => {}, onFrameUpdate: progress("captions", capTodo.length), concurrency, logLevel: "error",
      });
      // renderFrames numbers its files in frame order, zero-padded, so sorted names line up with capTodo.
      const drawn = readdirSync(dir).filter((f) => f.endsWith(".png")).sort();
      if (drawn.length !== capTodo.length) throw new Error(`Expected ${capTodo.length} caption images, got ${drawn.length}`);
      capTodo.forEach(([key], i) => renameSync(join(dir, drawn[i]), capFile(key)));
      execFileSync("rm", ["-rf", dir]);
      console.log(`drew ${capTodo.length} caption looks (${Math.round((Date.now() - started) / 1000)}s)`);
    }
    if (clipTodo.length) {
      const composition = await selectComposition({ serveUrl, id: "GraphicsLayer", inputProps: props, puppeteerInstance: browser });
      for (const c of clipTodo) {
        const tmp = `${c.file}.part.mov`;
        const drawn = progress("graphics", c.b - c.a);
        await renderMedia({
          serveUrl, composition, inputProps: props, puppeteerInstance: browser, codec: "prores", proResProfile: "4444",
          imageFormat: "png", pixelFormat: "yuva444p10le", muted: true, frameRange: [c.a, c.b - 1], outputLocation: tmp,
          overwrite: true, concurrency, logLevel: "error", onProgress: ({ renderedFrames }) => drawn(renderedFrames),
        });
        renameSync(tmp, c.file);
        console.log(`drew graphics ${(c.a / FPS).toFixed(1)}–${(c.b / FPS).toFixed(1)}s (${Math.round((Date.now() - started) / 1000)}s)`);
      }
    }
  } finally {
    await browser.close({ silent: true });
  }
  if (store) {
    for (const c of clipTodo) {
      const small = join(layers, "graphics", `${c.key}.webm`);
      execFileSync("ffmpeg", ["-v", "error", "-y", "-i", c.file, "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-deadline", "realtime",
        "-cpu-used", "8", "-row-mt", "1", "-b:v", "0", "-crf", "32", "-an", small]);
    }
    await inBatches([...capTodo.map(([key]) => capFile(key)), ...clipTodo.map((c) => join(layers, "graphics", `${c.key}.webm`))], saveLayer);
  }
}
// Graphics clips this reel no longer uses go, so a folder edited many times doesn't fill the disk.
const used = new Set(clips.flatMap((c) => [`${c.key}.mov`, `${c.key}.webm`]));
for (const f of readdirSync(join(layers, "graphics"))) if (!used.has(f)) rmSync(join(layers, "graphics", f), { force: true });

// ---- Encode the pieces that aren't there yet.
const blank = join(layers, "blank.png");
if (!existsSync(blank)) execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", `color=c=black@0.0:s=${W}x${H},format=rgba`, "-frames:v", "1", blank]);
// Grain and vignette, like Reel.tsx's Grain: fine moving noise, and the same radial darkening (clear to 55% of the
// way to the corners, then up to 28% black), drawn once as an image.
const vignette = join(layers, "vignette.png");
if (style.grain === true && !existsSync(vignette)) {
  const r = `hypot((X-${W / 2})/${W / 2},(Y-${H / 2})/${H / 2})/sqrt(2)`;
  execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", `color=c=black:s=${W}x${H},format=rgba`, "-vf",
    `geq=r=0:g=0:b=0:a='255*0.28*clip((${r}-0.55)/0.45,0,1)'`, "-frames:v", "1", vignette]);
}
const composeStart = Date.now();
await encodePieces(pieceTodo, { video: videoPath, captionFile: (name) => (name === "blank" ? blank : capFile(name)), vignette, grain: style.grain === true });
if (pieceTodo.length) console.log(`encoded ${pieceTodo.length} of ${pieces.length} pieces (${Math.round((Date.now() - composeStart) / 1000)}s)`);
// Pieces this reel no longer uses go, like its old graphics.
const kept = new Set(pieces.map((p) => basename(p.file)));
for (const f of readdirSync(pieceDir)) if (!kept.has(f)) rmSync(join(pieceDir, f), { force: true });

// ---- The sound: the cut's own, a pop when paper comes in, a tick for notes over the video, plus any sfx Ken asked for.
const SOUNDS = ["whoosh", "pop", "tick"];
const sfx = [
  ...graphics.map((g) => ({ at: g.start, sound: g.placement === "over" ? "tick" : "pop" })),
  ...(props.sfx ?? []),
].filter((s) => SOUNDS.includes(s.sound) && s.at >= 0 && s.at < props.durationSeconds);
joinPieces(pieces, { video: videoPath, frames: total, out, list: join(layers, "pieces.txt"),
  sfx: sfx.map((s) => ({ at: s.at, file: join(resolve("sfx"), `${s.sound}.wav`) })) });
console.log(`ffmpeg took ${Math.round((Date.now() - composeStart) / 1000)}s; all in ${Math.round((Date.now() - started) / 1000)}s`);
// Check the result before calling it done: every frame there, and sound as long as the picture. render.sh falls back
// to drawing the whole reel in Chrome when this fails.
const probe = probeJson(out, "stream=codec_type,duration,nb_frames");
const video = probe.streams.find((x) => x.codec_type === "video");
const audio = probe.streams.find((x) => x.codec_type === "audio");
const problems = [];
if (Number(video?.nb_frames) !== total) problems.push(`${video?.nb_frames} frames, expected ${total}`);
if (!audio || Math.abs(Number(audio.duration) - Number(video?.duration)) > 0.2) problems.push(`sound ${audio?.duration ?? "missing"}s, picture ${video?.duration}s`);
if (problems.length) {
  console.error(`The render didn't check out: ${problems.join("; ")}`);
  process.exit(1);
}
console.log(`Rendered ${out} (${(statSync(out).size / 1e6).toFixed(1)} MB)`);
