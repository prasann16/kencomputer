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
// On the sheet, the parts the apps cover with their own buttons (the top 250, bottom 480 and right 140 px of the
// 1080x1920 frame) are tinted red, so text that strays into them shows. The single stills stay clean.
const zones = ["0:0:iw:ih*250/1920", "0:ih-ih*480/1920:iw:ih*480/1920", "iw-iw*140/1080:0:iw*140/1080:ih"]
  .map((box) => `drawbox=${box}:color=red@0.12:t=fill`).join(",");
const marked = made.map((_, i) => `[${i}:v]${zones}[z${i}]`).join(";");
const sheet = join(dir, "sheet.jpg");
execFileSync("ffmpeg", ["-v", "error", "-y", ...made.flatMap((f) => ["-i", f]), "-filter_complex",
  made.length > 1 ? `${marked};${made.map((_, i) => `[z${i}]`).join("")}hstack=${made.length}` : `[0:v]${zones}`, sheet]);
console.log(`${made.length > 1 ? "All of them side by side" : "With the safe zones marked"}: ${sheet}`);
