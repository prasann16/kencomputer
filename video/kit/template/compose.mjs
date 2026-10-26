// Renders the reel the fast way: Chrome draws only what has to look designed (each distinct look of the captions,
// once, and the graphics for the seconds they're on screen, on a transparent background), and ffmpeg does the
// footage (framing, push-ins, the lift under paper, grain) and lays everything over it in one pass.
// Both kinds of drawing are kept by what's in them, so after a change only what changed is drawn again.
// Usage (from this folder): node compose.mjs <work_dir> <out.mp4> [--plan]
// --plan only says how much would be drawn, without drawing anything.
// Whip pans (chapters) aren't done here; render.mjs still does everything in Chrome if a reel needs them.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";

import { createTikTokStyleCaptions } from "@remotion/captions";
import { openBrowser, renderFrames, renderMedia, selectComposition } from "@remotion/renderer";

import { cachedBundle, codeHash, hash, oneAtATime } from "./lib.mjs";

const FPS = 30;
const W = 1080, H = 1920;
const SHEET_LIFT = 340; // as in src/theme.ts
const PLACEMENTS = { title: "full", chat: "full", picture: "full", nameTag: "over", callout: "over", punch: "over", browser: "over" };

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
  return c && /^\d+$/.test(c) ? Number(c) : c ?? null;
})();
const layers = join(work, ".layers");
mkdirSync(join(layers, "captions"), { recursive: true });
mkdirSync(join(layers, "graphics"), { recursive: true });
const code = codeHash();

const ease = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - (1 - x) ** 3);
const graphics = (props.graphics ?? []).map((g) => ({ ...g, placement: g.placement ?? PLACEMENTS[g.type] ?? "sheet" }));

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
    const tokens = page.tokens.map((t) => [t.text, Math.round(t.fromMs), emphasized.has(Math.round(t.fromMs))]);
    for (let f = from; f < Math.min(total, from + duration); f++) {
      const nowMs = start * 1000 + ((f - from) / FPS) * 1000;
      const shown = page.tokens.map((t) => +ease((nowMs - t.fromMs) / 120).toFixed(3));
      looks[f] = { key: hash([code, style, tokens, shown]).slice(0, 24), frame: f };
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
  const key = hash([
    code, style, b - a,
    graphics.filter((g) => g.start < t1 && g.end > t0).map((g) => ({ ...g, start: rel(g.start), end: rel(g.end) })),
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
const fetchLayer = async (file) => {
  const res = await fetch(`${store}/${basename(file)}`).catch(() => null);
  if (res?.ok) writeFileSync(file, Buffer.from(await res.arrayBuffer()));
};
const saveLayer = (file) => fetch(`${store}/${basename(file)}`, { method: "PUT", body: readFileSync(file) }).catch(() => null);
if (store) {
  await inBatches([...firstOf.keys()].filter((key) => !existsSync(capFile(key))).map(capFile), fetchLayer);
  await inBatches(clips.filter((c) => !existsSync(c.file)), async (c) => {
    c.file = join(layers, "graphics", `${c.key}.webm`);
    await fetchLayer(c.file);
    if (!existsSync(c.file)) c.file = join(layers, "graphics", `${c.key}.mov`);
  });
}
const capTodo = [...firstOf].filter(([key]) => !existsSync(capFile(key)));
const clipTodo = clips.filter((c) => !existsSync(c.file));

console.log(`captions: ${firstOf.size} looks, ${capTodo.length} to draw; graphics: ${clips.length} clips (${clips.reduce((n, c) => n + c.b - c.a, 0)} frames), ${clipTodo.length} to draw`);
if (plan) process.exit(0);

// ---- Draw what's missing.
const started = Date.now();
if (capTodo.length || clipTodo.length) {
  await oneAtATime("compose");
  const serveUrl = await cachedBundle(work, props, { video: false });
  const browser = await openBrowser("chrome");
  try {
    if (capTodo.length) {
      const inputProps = { ...props, pick: capTodo.map(([, f]) => f) };
      const composition = await selectComposition({ serveUrl, id: "CaptionsLayer", inputProps, puppeteerInstance: browser });
      const dir = join(layers, "captions", `.draw-${process.pid}`);
      await renderFrames({
        serveUrl, composition, inputProps, puppeteerInstance: browser, imageFormat: "png", outputDir: dir,
        frameRange: [0, capTodo.length - 1], onStart: () => {}, onFrameUpdate: () => {}, concurrency, logLevel: "error",
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
        await renderMedia({
          serveUrl, composition, inputProps: props, puppeteerInstance: browser, codec: "prores", proResProfile: "4444",
          imageFormat: "png", pixelFormat: "yuva444p10le", muted: true, frameRange: [c.a, c.b - 1], outputLocation: tmp,
          overwrite: true, concurrency, logLevel: "error",
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

// ---- The captions as one transparent video: each look held until the next one.
const blank = join(layers, "blank.png");
if (!existsSync(blank)) execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", `color=c=black@0.0:s=${W}x${H},format=rgba`, "-frames:v", "1", blank]);
const runs = [];
for (let f = 0; f < total; f++) {
  const file = looks[f] ? capFile(looks[f].key) : blank;
  if (runs.length && runs[runs.length - 1].file === file) runs[runs.length - 1].frames++;
  else runs.push({ file, frames: 1 });
}
const capList = join(layers, "captions.txt");
// Each image is read at 30 fps (ffmpeg's default for a still is 25, which would put captions up to 40 ms off).
const entry = (file) => `file '${file}'\noption framerate ${FPS}`;
writeFileSync(capList, "ffconcat version 1.0\n" + runs.map((r) => `${entry(r.file)}\nduration ${(r.frames / FPS).toFixed(6)}`).join("\n") + `\n${entry(runs[runs.length - 1].file)}\n`);

// ---- The footage: each part's framing, the push-ins, and the lift while paper is up, as ffmpeg expressions of
// time. The perspective filter samples between pixels, so slow zooms stay smooth instead of stepping a pixel at a time.
const T = `(in/${FPS})`;
const E = (x) => `(1-pow(1-clip(${x},0,1),3))`;
let scale = "1";
if (style.zoomCuts ?? true) {
  if (props.framing) for (const f of props.framing) scale = `if(gte(${T},${f.at - 1e-6}),${f.scale},${scale})`;
  else for (const c of props.cuts ?? []) scale = `if(gte(${T},${c}),${scale === "1" ? 1.06 : 1},${scale})`;
}
for (const z of props.zooms ?? []) {
  scale = `(${scale})*if(between(${T},${z.start},${z.end}),1+0.14*min(${E(`(${T}-${z.start})/1.2`)},${E(`(${z.end}-${T})/0.25`)}),1)`;
}
let lift = "0";
for (const g of graphics.filter((g) => g.placement === "sheet")) {
  lift = `max(${lift},if(between(${T},${g.start},${g.end}),min(${E(`(${T}-${g.start})/0.4`)},${E(`(${g.end}-${T})/0.3`)}),0))`;
}
lift = `(${SHEET_LIFT}*${lift})`;
// Each corner of the frame shows this point of the source: centre + (corner - centre + lift) / scale.
const x = (side) => `${W / 2}${side}${W / 2}/(${scale})`;
const y = (side) => `${H / 2}+(${lift}${side}${H / 2})/(${scale})`;
// It's skipped (the frame passes through untouched) while the framing is 100% and nothing is lifting.
const moved = `gt(abs((${scale})-1)+${lift},0.0001)`.replaceAll(T, "t");
const perspective = `perspective=x0='${x("-")}':y0='${y("-")}':x1='${x("+")}':y1='${y("-")}':x2='${x("-")}':y2='${y("+")}':x3='${x("+")}':y3='${y("+")}':interpolation=linear:eval=frame:enable='${moved}'`;

// ---- Sound: a pop when paper comes in, a tick for notes over the video, plus any sfx Ken asked for.
const SOUNDS = ["whoosh", "pop", "tick"];
const sfx = [
  ...graphics.map((g) => ({ at: g.start, sound: g.placement === "over" ? "tick" : "pop" })),
  ...(props.sfx ?? []),
].filter((s) => SOUNDS.includes(s.sound) && s.at >= 0 && s.at < props.durationSeconds);

// ---- One pass: footage, graphics, captions, grain, sound.
const inputs = ["-i", join(work, props.video), "-f", "concat", "-safe", "0", "-i", capList];
// VP9 keeps its transparency only through libvpx's own decoder.
clips.forEach((c) => inputs.push(...(c.file.endsWith(".webm") ? ["-c:v", "libvpx-vp9"] : []), "-itsoffset", (c.a / FPS).toFixed(4), "-i", c.file));
sfx.forEach((s) => inputs.push("-i", join(resolve("sfx"), `${s.sound}.wav`)));
// Frames exactly every 1/30 s from zero, a missing one filled by repeating the last and the end padded to the full
// length, so frame n is time n/30 for the expressions above and the overlays.
const vf = [`[0:v]fps=${FPS}:start_time=0,tpad=stop_mode=clone:stop_duration=2,${perspective}[v0]`];
let v = "v0";
clips.forEach((c, i) => {
  vf.push(`[${v}][${i + 2}:v]overlay=eof_action=pass:format=auto[g${i}]`);
  v = `g${i}`;
});
vf.push(`[1:v]fps=${FPS},format=rgba[cap]`, `[${v}][cap]overlay=eof_action=pass:format=auto:shortest=0[c]`);
// Grain and vignette, like Reel.tsx's Grain: fine moving noise, and the same radial darkening (clear to 55% of the
// way to the corners, then up to 28% black), drawn once as an image.
const vignette = join(layers, "vignette.png");
if (!existsSync(vignette)) {
  const r = `hypot((X-${W / 2})/${W / 2},(Y-${H / 2})/${H / 2})/sqrt(2)`;
  execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", `color=c=black:s=${W}x${H},format=rgba`, "-vf",
    `geq=r=0:g=0:b=0:a='255*0.28*clip((${r}-0.55)/0.45,0,1)'`, "-frames:v", "1", vignette]);
}
inputs.push("-loop", "1", "-i", vignette);
const vig = 2 + clips.length + sfx.length;
vf.push(style.grain !== false ? `[c][${vig}:v]overlay=shortest=1:format=auto,noise=c0s=4:c0f=t+u,format=yuv420p[v]` : `[c]format=yuv420p[v]`);
const sfx0 = 2 + clips.length;
const af = sfx.length
  ? [sfx.map((s, i) => `[${sfx0 + i}:a]adelay=${Math.round(s.at * 1000)}:all=1[s${i}]`).join(";") + `;[0:a]${sfx.map((_, i) => `[s${i}]`).join("")}amix=inputs=${sfx.length + 1}:normalize=0:duration=first[a]`]
  : [];
const graph = join(layers, "graph.txt");
writeFileSync(graph, [...vf, ...af].join(";\n"));
const composeStart = Date.now();
execFileSync("ffmpeg", [
  "-v", "error", "-y", ...inputs, "-filter_complex_script", graph, "-map", "[v]", "-map", sfx.length ? "[a]" : "0:a",
  "-frames:v", String(total), "-c:v", "libx264", "-crf", "19", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k",
  "-movflags", "+faststart", out,
], { stdio: "inherit" });
console.log(`ffmpeg took ${Math.round((Date.now() - composeStart) / 1000)}s; all in ${Math.round((Date.now() - started) / 1000)}s`);
// Check the result before calling it done: every frame there, and sound as long as the picture. render.sh falls back
// to drawing the whole reel in Chrome when this fails.
const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_entries", "stream=codec_type,duration,nb_frames", "-of", "json", out]).toString());
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
