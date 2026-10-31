// Shared by render.mjs and stills.mjs: one Remotion job at a time, and one bundle per version of the code and cut.
import { createHash } from "node:crypto";
import { existsSync, linkSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";

import { bundle } from "@remotion/bundler";

export const hash = (value) => createHash("sha256").update(typeof value === "string" ? value : JSON.stringify(value)).digest("hex");

const codeFiles = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? codeFiles(join(dir, e.name)) : [join(dir, e.name)]));
// The project's code (custom graphics included).
export const codeHash = () => hash(codeFiles("src").sort().map((f) => [f, readFileSync(f, "utf8")]));

// A killed render nobody has reaped yet (on Linux, a "zombie") still answers kill(pid, 0) but holds nothing.
const zombie = (pid) => {
  try {
    return /\) Z /.test(readFileSync(`/proc/${pid}/stat`, "utf8"));
  } catch {
    return false;
  }
};

// Each Remotion job opens Chrome and uses about a gigabyte, so only one runs at a time on this machine;
// a second one waits its turn instead of piling on.
export async function oneAtATime(label) {
  const lock = join(tmpdir(), "ken-remotion.lock");
  for (let waited = 0; ; waited += 2) {
    try {
      mkdirSync(lock);
      writeFileSync(join(lock, "pid"), String(process.pid));
      break;
    } catch {
      const owner = Number(existsSync(join(lock, "pid")) ? readFileSync(join(lock, "pid"), "utf8") : 0);
      let alive = false;
      try {
        process.kill(owner, 0);
        alive = owner > 0 && !zombie(owner);
      } catch {}
      if (!alive && waited >= 4) rmSync(lock, { recursive: true, force: true });
      if (waited === 0) console.log(`${label}: waiting for another render to finish…`);
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  const release = () => rmSync(lock, { recursive: true, force: true });
  process.on("exit", release);
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => process.exit(1));
  return release;
}

// The bundle (code + the files the reel uses), built once and reused until the code or those files change.
// Only the files props.json points at go in, linked rather than copied, so a work folder full of checks and
// rendered pieces doesn't get copied every time. compose.mjs never shows the video itself ({video: false}), so for
// a reel without pictures the bundle is the same for every reel: it's kept next to the code, where the box image
// builds it ahead of time.
export async function cachedBundle(work, props, { video = true } = {}) {
  const wanted = new Set(video ? [props.video] : []);
  for (const g of props.graphics ?? []) if (typeof g.props?.src === "string") wanted.add(g.props.src);
  const files = [...wanted].filter((f) => f && work && existsSync(join(work, f)));
  const key = hash([codeHash(), files.map((f) => [f, statSync(join(work, f)).size, statSync(join(work, f)).mtimeMs])]).slice(0, 16);
  // KEN_BUNDLE_DIR: where a machine built with the kit keeps the bundle it made ahead of time.
  const root = files.length ? join(work, ".bundle") : resolve(process.env.KEN_BUNDLE_DIR ?? ".bundle");
  const out = join(root, key);
  if (existsSync(join(out, "index.html"))) return out;

  rmSync(root, { recursive: true, force: true });
  const pub = join(root, "public");
  mkdirSync(pub, { recursive: true });
  for (const f of files) {
    mkdirSync(join(pub, f, ".."), { recursive: true });
    linkSync(join(work, f), join(pub, f));
  }
  await bundle({ entryPoint: resolve("src/index.ts"), publicDir: pub, outDir: out });
  rmSync(pub, { recursive: true, force: true });
  return out;
}
