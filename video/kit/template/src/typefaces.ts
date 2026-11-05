// Fonts for a reel. Inter, Instrument Serif and Montserrat Black are bundled (they work offline); any other family is
// loaded from Google Fonts by name when the reel renders. A family that can't be loaded falls back to Inter and says so
// in the render's log, rather than holding the render up.
import { loadFont as loadLocal } from "@remotion/fonts";
import { cancelRender, continueRender, delayRender } from "remotion";

import interUrl from "./fonts/Inter.ttf";
import serifItalicUrl from "./fonts/InstrumentSerif-Italic.ttf";
import serifUrl from "./fonts/InstrumentSerif-Regular.ttf";
import montserratUrl from "./fonts/Montserrat-Black.ttf";

const BUNDLED: Record<string, () => Promise<unknown>> = {
  Inter: () => loadLocal({ family: "Inter", url: interUrl, weight: "100 900" }),
  "Instrument Serif": () =>
    Promise.all([
      loadLocal({ family: "Instrument Serif", url: serifUrl, style: "normal" }),
      loadLocal({ family: "Instrument Serif", url: serifItalicUrl, style: "italic" }),
    ]),
  Montserrat: () => loadLocal({ family: "Montserrat", url: montserratUrl, weight: "900" }),
};

const started = new Map<string, Promise<void>>();
const WEIGHTS = [100, 200, 300, 400, 500, 600, 700, 800, 900];

// The CSS for a Google family: every weight and italic it has (asking for ones it lacks is an error, so fall back
// step by step to fewer).
async function googleCss(name: string): Promise<string> {
  const fam = encodeURIComponent(name).replace(/%20/g, "+");
  const tries = [
    `${fam}:ital,wght@${[...WEIGHTS.map((w) => `0,${w}`), ...WEIGHTS.map((w) => `1,${w}`)].join(";")}`,
    `${fam}:wght@${WEIGHTS.join(";")}`,
    `${fam}:ital@0;1`,
    fam,
  ];
  for (const q of tries) {
    const r = await fetch(`https://fonts.googleapis.com/css2?family=${q}&display=block`).catch(() => null);
    if (r?.ok) return r.text();
  }
  throw new Error(`Google Fonts has no family called "${name}"`);
}

// Load a family (idempotent). The render waits for it, up to 20 seconds.
export function loadFont(name: string): Promise<void> {
  const existing = started.get(name);
  if (existing) return existing;
  const handle = delayRender(`Loading font ${name}`, { timeoutInMilliseconds: 25_000 });
  const load = (async () => {
    if (BUNDLED[name]) {
      await BUNDLED[name]();
      return;
    }
    const css = await googleCss(name);
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    const faces = [...css.matchAll(/font-style:\s*(\w+);\s*font-weight:\s*(\d+)/g)].map((m) => `${m[1]} ${m[2]} 40px "${name}"`);
    await Promise.all((faces.length ? faces : [`400 40px "${name}"`]).map((f) => document.fonts.load(f)));
  })();
  const done = Promise.race([load, new Promise((_, reject) => setTimeout(() => reject(new Error("timed out")), 20_000))])
    .catch((e) => console.warn(`Font "${name}" not loaded (${e instanceof Error ? e.message : e}); using Inter instead.`))
    .then(() => continueRender(handle))
    .catch((e) => cancelRender(e));
  started.set(name, done);
  return done;
}

// A CSS font stack for a family, falling back to Inter.
export const fontStack = (name: string) => `"${name}", Inter, sans-serif`;

// Bundled fonts every reel can use without the network.
loadFont("Inter");
