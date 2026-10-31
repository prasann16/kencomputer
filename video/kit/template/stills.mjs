// Check frames of the reel without rendering it: one Chrome, one frame after another, plus a contact sheet.
// Usage (from this folder): node stills.mjs <work_dir> <seconds...>
// Writes <work_dir>/checks/<seconds>.jpg and <work_dir>/checks/sheet.jpg (all of them side by side).
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { openBrowser, renderStill, selectComposition } from "@remotion/renderer";

import { cachedBundle, oneAtATime } from "./lib.mjs";

const [workArg, ...times] = process.argv.slice(2);
if (!workArg || !times.length) {
  console.error("Usage: node stills.mjs <work_dir> <seconds...>");
  process.exit(2);
}
const work = resolve(workArg);
const props = JSON.parse(readFileSync(join(work, "props.json"), "utf8"));
const dir = join(work, "checks");
mkdirSync(dir, { recursive: true });

await oneAtATime("stills");
const serveUrl = await cachedBundle(work, props);
const browser = await openBrowser("chrome");
const made = [];
try {
  const composition = await selectComposition({ serveUrl, id: "Reel", inputProps: props, puppeteerInstance: browser });
  for (const t of times) {
    const frame = Math.min(composition.durationInFrames - 1, Math.max(0, Math.round(Number(t) * composition.fps)));
    const output = join(dir, `${t}.jpg`);
    await renderStill({ serveUrl, composition, inputProps: props, puppeteerInstance: browser, frame, output, imageFormat: "jpeg", jpegQuality: 85, scale: 0.5, overwrite: true });
    made.push(output);
    console.log(`${t}s -> ${output}`);
  }
} finally {
  await browser.close({ silent: true });
}
if (made.length > 1) {
  const sheet = join(dir, "sheet.jpg");
  execFileSync("ffmpeg", ["-v", "error", "-y", ...made.flatMap((f) => ["-i", f]), "-filter_complex", `${made.map((_, i) => `[${i}:v]`).join("")}hstack=${made.length}`, sheet]);
  console.log(`All of them side by side: ${sheet}`);
}
