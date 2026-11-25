// Builds the live preview of a reel into <work>/.preview/: a page that plays it in the browser with nothing rendered
// (see preview.tsx). The page reads the reel's props.json and footage from the folder itself, so the build is only
// redone when the code changes. Usage (from this folder): node preview.mjs <work_dir>
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { build } from "esbuild";

import { codeHash, hash } from "./plan.mjs";

const work = process.argv[2] && resolve(process.argv[2]);
if (!work || !existsSync(join(work, "props.json"))) {
  console.error("Usage: node preview.mjs <work_dir> (a reel folder with props.json)");
  process.exit(2);
}
const out = join(work, ".preview");
const key = hash([codeHash(), readFileSync("preview.tsx", "utf8")]).slice(0, 16);
const stamp = join(out, `${key}.built`);
if (!existsSync(stamp)) {
  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  await build({
    entryPoints: [resolve("preview.tsx")], bundle: true, minify: true, format: "esm", target: "es2020", outfile: join(out, "preview.js"), publicPath: ".preview",
    loader: { ".ttf": "file", ".woff2": "file", ".woff": "file", ".otf": "file", ".png": "file", ".jpg": "file", ".svg": "file" },
    jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, logLevel: "error",
  });
  writeFileSync(stamp, "");
}
// The fonts the reel loads from Google, saved beside the page (it may only load files from where it's served): each
// family's CSS, pointing at its font files here. Bundled families (src/typefaces.ts) need nothing.
const BUNDLED = new Set(["Inter", "Instrument Serif", "Montserrat"]);
const style = JSON.parse(readFileSync(join(work, "props.json"), "utf8")).style ?? {};
const families = [...new Set([style.font, style.headingFont, style.accentFont, ...(style.fonts ?? [])])].filter((f) => typeof f === "string" && f && !BUNDLED.has(f));
const fontDir = join(out, "fonts");
mkdirSync(fontDir, { recursive: true });
const UA = { "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36" };
const WEIGHTS = [100, 200, 300, 400, 500, 600, 700, 800, 900];
for (const name of families) {
  const saved = join(fontDir, `${encodeURIComponent(name)}.css`);
  if (existsSync(saved)) continue;
  const fam = encodeURIComponent(name).replace(/%20/g, "+");
  let css = null;
  for (const q of [`${fam}:ital,wght@${[...WEIGHTS.map((w) => `0,${w}`), ...WEIGHTS.map((w) => `1,${w}`)].join(";")}`, `${fam}:wght@${WEIGHTS.join(";")}`, `${fam}:ital@0;1`, fam]) {
    const r = await fetch(`https://fonts.googleapis.com/css2?family=${q}&display=block`, { headers: UA }).catch(() => null);
    if (r?.ok) {
      css = await r.text();
      break;
    }
  }
  if (!css) {
    console.log(`Font "${name}" isn't on Google Fonts; the preview shows Inter there.`);
    continue;
  }
  let n = 0;
  for (const url of [...new Set([...css.matchAll(/url\((https:\/\/fonts\.gstatic\.com\/[^)]+)\)/g)].map((x) => x[1]))]) {
    const file = `${hash([name, url]).slice(0, 12)}.${url.split(".").pop()}`;
    const r = await fetch(url, { headers: UA }).catch(() => null);
    if (!r?.ok) continue;
    writeFileSync(join(fontDir, file), Buffer.from(await r.arrayBuffer()));
    css = css.replaceAll(url, `.preview/fonts/${file}`);
    n++;
  }
  writeFileSync(saved, css);
  console.log(`saved font ${name} (${n} files)`);
}

// The page sits in the reel's folder, beside props.json and the footage: files the reel points at load from there.
writeFileSync(join(work, "preview.html"), `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Reel preview</title>
<style>
  html, body { margin: 0; height: 100%; background: #000; }
  #app { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; }
  #app > div { max-width: 100vw; max-height: 100dvh; aspect-ratio: 9 / 16; }
  .msg { color: #ccc; font: 16px system-ui, sans-serif; text-align: center; padding: 24px; }
</style>
</head>
<body>
<div id="app"></div>
<script type="module" src=".preview/preview.js?${key}"></script>
</body>
</html>
`);
console.log(`Preview ready: ${join(work, "preview.html")}`);
