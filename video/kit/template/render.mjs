// Renders the reel in 3-second pieces and keeps them, so after a small change only the pieces it touches
// render again; the rest come from the cache and everything is joined in seconds.
// Usage (from this folder): node render.mjs <work_dir> <out.mp4> [--draft] [--plan]
// --plan only says how many pieces would render, without rendering anything.
// --draft renders at half size, about 3 to 4 times faster: for checking the story, cuts and graphics before
// the full-quality render. Draft and full pieces are cached separately.
//
// Each piece is named by a fingerprint of everything that can change how it looks: the video under it,
// the caption pages, graphics and jump cuts near it, the title, the style and this project's code.
// Pieces live in KEN_RENDER_CACHE (default <work_dir>/chunks) and, in the cloud box, in the user's
// render cache behind KEN_BOX_URL, so a fix in a later job reuses them too.
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { createTikTokStyleCaptions } from "@remotion/captions";
import { renderMedia, selectComposition } from "@remotion/renderer";

import { cachedBundle, codeHash, hash, oneAtATime, openChrome } from "./lib.mjs";

const FPS = 30;
const CHUNK = 90; // frames (3 s)
const CRF = 19;
const VERSION = "3"; // bump to throw away every cached piece

const args = process.argv.slice(2);
const draft = args.includes("--draft");
const plan = args.includes("--plan");
const [work, out] = args.filter((a) => !a.startsWith("--")).map((p) => p && resolve(p));
const SCALE = draft ? 0.5 : 1;
if (!work || !out) {
  console.error("Usage: node render.mjs <work_dir> <out.mp4>");
  process.exit(2);
}
const props = JSON.parse(readFileSync(join(work, "props.json"), "utf8"));
const cache = resolve(process.env.KEN_RENDER_CACHE || join(work, "chunks"));
const env = process.env.KEN_RENDER_CONCURRENCY || "100%";
const concurrency = /^\d+$/.test(env) ? Number(env) : env; // a number of tabs, or a share of the CPUs like "50%"
const remote = process.env.KEN_BOX_URL ? `${process.env.KEN_BOX_URL}/cache` : null;
mkdirSync(cache, { recursive: true });


// The project's code (custom graphics included): a change there re-renders everything.
const code = codeHash();

// What's under the reel: the source pieces cut.py kept, or the cut video itself when there's no cut list.
const cutlist = existsSync(join(work, "cutlist.json")) ? JSON.parse(readFileSync(join(work, "cutlist.json"), "utf8")) : null;
const videoPath = join(work, props.video);
const wholeVideo = cutlist?.pieces && cutlist.source ? null : hash(readFileSync(videoPath));
const videoUnder = (t0, t1) => {
  if (wholeVideo) return wholeVideo;
  const speed = cutlist.speed ?? 1;
  const under = [];
  let pos = 0;
  for (const [a, b] of cutlist.pieces) {
    const length = (b - a) / speed;
    const from = Math.max(t0, pos), to = Math.min(t1, pos + length);
    if (from < to) under.push([+(a + (from - pos) * speed).toFixed(3), +(a + (to - pos) * speed).toFixed(3)]);
    pos += length;
  }
  return [cutlist.source, speed, under];
};

// Caption pages exactly as Reel.tsx makes them.
const words = props.words ?? [];
const pages = createTikTokStyleCaptions({
  captions: words.map((w) => ({ text: ` ${w.text.trim()}`, startMs: w.start * 1000, endMs: w.end * 1000, timestampMs: null, confidence: null })),
  combineTokensWithinMilliseconds: props.style?.wordsPerPageMs ?? 1100,
}).pages;

const graphics = props.graphics ?? [];
const known = new Set(["video", "durationSeconds", "words", "cuts", "framing", "hook", "hookSeconds", "title", "titleSeconds", "chapters", "zooms", "graphics", "style", "sfx"]);
const hookSeconds = props.hookSeconds ?? props.titleSeconds ?? 2.8;
const rest = Object.fromEntries(Object.entries(props).filter(([k]) => !known.has(k)));
const total = Math.max(1, Math.round(props.durationSeconds * FPS));

// Pieces start at every cut, then every 3 s. Each is named by what's in it, timed from its own start, not by
// where it sits in the reel: when a sentence is dropped or added, everything else keeps its name and is reused.
const starts = new Set([0]);
for (const c of [...(props.cuts ?? []), ...(props.framing ?? []).map((f) => f.at)]) starts.add(Math.round(c * FPS));
const bounds = [...starts].filter((f) => f > 0 && f < total).sort((x, y) => x - y);
const edges = [0];
for (const next of [...bounds, total]) {
  for (let f = edges[edges.length - 1] + CHUNK; f < next - CHUNK / 3; f += CHUNK) edges.push(f);
  edges.push(next);
}
const framingAt = (t) => (props.framing ?? []).filter((f) => f.at <= t + 1e-6).pop()?.scale ?? null;

const chunks = [];
for (let i = 0; i + 1 < edges.length; i++) {
  const a = edges[i], b = edges[i + 1] - 1;
  if (b < a) continue;
  const t0 = a / FPS, t1 = (b + 1) / FPS;
  const rel = (t) => Math.round((t - t0) * FPS); // in frames from this piece's start
  const parts = {
    VERSION, CRF, SCALE, code, rest,
    frames: b - a + 1,
    style: props.style ?? {},
    video: videoUnder(t0, t1),
    // A page can stay up to 400 ms past its last word; a sheet or full graphic near it can delay it.
    pages: pages
      .filter((p) => p.startMs / 1000 < t1 && (p.startMs + p.durationMs + 400) / 1000 > t0)
      .map((p) => [rel(p.startMs / 1000), rel((p.startMs + p.durationMs) / 1000), p.tokens.map((k) => [k.text, rel(k.fromMs / 1000), rel(k.toMs / 1000)])]),
    emph: words.filter((w) => w.emph && w.start < t1 + 1 && w.end > t0 - 3).map((w) => rel(w.start)),
    graphics: graphics.filter((g) => g.start < t1 && g.end > t0 - 3).map((g) => ({ ...g, start: rel(g.start), end: rel(g.end) })),
    hook: hookSeconds > t0 ? [props.hook ?? props.title, hookSeconds, a] : null,
    chapters: (props.chapters ?? []).filter((c) => c > t0 - 0.3 && c < t1 + 0.3).map(rel),
    zooms: (props.zooms ?? []).filter((z) => z.start < t1 && z.end > t0).map((z) => [rel(z.start), rel(z.end)]),
    framing: props.framing
      ? [framingAt(t0 + 0.5 / FPS), (props.framing ?? []).filter((f) => f.at > t0 + 0.5 / FPS && f.at < t1).map((f) => [rel(f.at), f.scale])]
      : [(props.cuts ?? []).filter((c) => c <= t0).length % 2, (props.cuts ?? []).filter((c) => c > t0 && c < t1).map(rel)],
  };
  const key = hash(parts);
  chunks.push({ a, b, key, file: join(cache, `${key}.mp4`) });
  if (process.env.KEN_RENDER_DEBUG) writeFileSync(join(process.env.KEN_RENDER_DEBUG, `${a}.json`), JSON.stringify(parts, null, 1));
}

if (process.env.KEN_RENDER_DEBUG) process.exit(0);

// Fetch what we can from the cloud cache.
if (remote) {
  for (const c of chunks.filter((c) => !existsSync(c.file))) {
    const res = await fetch(`${remote}/${c.key}`).catch(() => null);
    if (res?.ok) writeFileSync(c.file, Buffer.from(await res.arrayBuffer()));
  }
}

const todo = chunks.filter((c) => !existsSync(c.file));
console.log(`${chunks.length} pieces, ${chunks.length - todo.length} already rendered, ${todo.length} to render`);
if (plan) process.exit(0);

if (todo.length) {
  const started = Date.now();
  await oneAtATime("render");
  const serveUrl = await cachedBundle(work, props);
  const browser = await openChrome();
  try {
    const composition = await selectComposition({ serveUrl, id: "Reel", inputProps: props, puppeteerInstance: browser });
    for (const [i, c] of todo.entries()) {
      const tmp = `${c.file}.part.mp4`;
      await renderMedia({
        serveUrl, composition, inputProps: props, puppeteerInstance: browser,
        codec: "h264", crf: CRF, scale: SCALE, muted: true, frameRange: [c.a, c.b], outputLocation: tmp, overwrite: true,
        concurrency, logLevel: "error",
      });
      copyFileSync(tmp, c.file);
      execFileSync("rm", ["-f", tmp]);
      if (remote) {
        await fetch(`${remote}/${c.key}`, { method: "PUT", body: readFileSync(c.file) }).catch(() => null);
      }
      console.log(`rendered piece ${i + 1}/${todo.length} (${(c.a / FPS).toFixed(1)}s)`);
    }
  } finally {
    await browser.close({ silent: true });
  }
  console.log(`rendering took ${Math.round((Date.now() - started) / 1000)}s`);
}

// Sound: a whoosh on each whip pan, a soft pop when paper comes in, a tick for notes over the video, plus any
// sfx Ken asked for ({at, sound}). Mixed under the cut's own audio.
const SOUNDS = ["whoosh", "pop", "tick"];
const placementOf = (g) => g.placement ?? { title: "full", chat: "full", picture: "full", nameTag: "over", callout: "over", punch: "over" }[g.type] ?? "sheet";
const sfx = [
  ...(props.chapters ?? []).map((c) => ({ at: c - 0.25, sound: "whoosh" })),
  ...graphics.map((g) => ({ at: g.start, sound: placementOf(g) === "over" ? "tick" : "pop" })),
  ...(props.sfx ?? []),
].filter((s) => SOUNDS.includes(s.sound) && s.at >= 0 && s.at < props.durationSeconds);

// Join the pieces (no re-encoding) and put the cut's audio under them.
const list = join(cache, `${hash(chunks.map((c) => c.key)).slice(0, 12)}.txt`);
writeFileSync(list, chunks.map((c) => `file '${c.file}'`).join("\n") + "\n");
const sfxDir = resolve("sfx");
const inputs = sfx.flatMap((s) => ["-i", join(sfxDir, `${s.sound}.wav`)]);
const mix = sfx.length
  ? ["-filter_complex", sfx.map((s, i) => `[${i + 2}:a]adelay=${Math.round(s.at * 1000)}:all=1[s${i}]`).join(";") +
      `;[1:a]${sfx.map((_, i) => `[s${i}]`).join("")}amix=inputs=${sfx.length + 1}:normalize=0:duration=first[a]`, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
  : ["-map", "0:v", "-map", "1:a", "-c", "copy"];
execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", list, "-i", videoPath, ...inputs, ...mix,
  "-shortest", "-movflags", "+faststart", out], { stdio: "inherit" });
console.log(`Rendered ${out} (${(statSync(out).size / 1e6).toFixed(1)} MB)`);
process.exit(0);
