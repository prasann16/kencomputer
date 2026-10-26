// The whole reel: the cut video with punch-ins, whip pans and push-ins, graphics, a hook headline and captions.
// Everything comes from props.json, so most reels need no code. For something new, add a graphic in src/graphics/.
import { createTikTokStyleCaptions, type Caption } from "@remotion/captions";
import { type ReactNode, useMemo } from "react";
import { AbsoluteFill, Freeze, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

import { useExit } from "./anim";
import { GRAPHICS, type Placement } from "./graphics";
import { type AreaBox, AreaContext, FONT_SERIF, paperStyle, Rich } from "./graphics/layout";
import { SAFE, SHEET_LIFT, SHEET_TOP, softShadow, type Style, ThemeContext, themeFrom, useTheme } from "./theme";

export interface Word {
  text: string;
  start: number; // seconds on the cut's timeline
  end: number;
  emph?: boolean; // set in the accent serif italic (about one word per sentence)
}

export interface GraphicSpec {
  type: string; // a key of GRAPHICS
  start: number;
  end: number;
  props?: Record<string, unknown>;
  placement?: Placement; // override the graphic's default
  hideCaptions?: boolean; // default: true for sheet and full graphics, false for ones over the video
}

export interface ReelProps {
  video: string; // in the public dir (render with --public-dir)
  durationSeconds: number;
  words: Word[];
  cuts?: number[]; // jump-cut times, written by cut.py
  framing?: { at: number; scale: number }[]; // each part's framing from that time on, written by cut.py
  hook?: string; // the headline over the first seconds; *starred words* in the accent serif
  hookSeconds?: number;
  title?: string; // older name for hook
  titleSeconds?: number;
  chapters?: number[]; // cut times where a new section starts: a whip pan and a whoosh
  zooms?: { start: number; end: number }[]; // slow push-ins on lines that matter
  graphics?: GraphicSpec[];
  style?: Style;
  [key: string]: unknown;
}

const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - (1 - x) ** 3);
const clamp01 = (x: number) => Math.max(0, Math.min(1, x));

// How far a sheet or full graphic is in, 0 → 1 → 0, at time t.
export function presence(g: { start: number; end: number }, t: number, inS = 0.4, outS = 0.3) {
  if (t < g.start || t >= g.end) return 0;
  return Math.min(ease((t - g.start) / inS), ease((g.end - t) / outS));
}

const placedGraphics = (props: ReelProps) =>
  (props.graphics ?? []).map((g) => ({ ...g, placement: g.placement ?? GRAPHICS[g.type]?.placement ?? "over" }));
// Captions step aside while paper covers the lower half; the voice keeps playing.
const hiddenFor = (placed: ReturnType<typeof placedGraphics>) =>
  placed.filter((g) => g.hideCaptions ?? g.placement !== "over").map((g) => [g.start, g.end] as const);

export const Reel = (props: ReelProps) => {
  const theme = themeFrom(props.style);
  const placed = placedGraphics(props);

  return (
    <ThemeContext.Provider value={theme}>
      <AbsoluteFill style={{ backgroundColor: "black" }}>
        <Speaker {...props} sheets={placed.filter((g) => g.placement === "sheet")} />
        <Graphics {...props} />
        {props.style?.captions !== false && <Captions words={props.words} hidden={hiddenFor(placed)} style={props.style ?? {}} />}
        <HookLayer {...props} />
        {props.style?.grain !== false && <Grain />}
      </AbsoluteFill>
    </ThemeContext.Provider>
  );
};

// The layers compose.mjs asks for, on a transparent background: Chrome draws only these, and ffmpeg moves the
// footage, lays them over it and adds the grain, which is much faster than drawing every frame of the video here.
export const GraphicsLayer = (props: ReelProps) => (
  <ThemeContext.Provider value={themeFrom(props.style)}>
    <AbsoluteFill>
      <Graphics {...props} />
      <HookLayer {...props} />
    </AbsoluteFill>
  </ThemeContext.Provider>
);

// Captions only change when a word appears, so compose.mjs asks for each distinct look once: frame i of this
// composition is the captions as they look at frame pick[i] of the reel.
export const CaptionsLayer = (props: ReelProps & { pick: number[] }) => {
  const frame = useCurrentFrame();
  return (
    <ThemeContext.Provider value={themeFrom(props.style)}>
      <Freeze frame={props.pick[frame] ?? 0}>
        <Captions words={props.words} hidden={hiddenFor(placedGraphics(props))} style={props.style ?? {}} />
      </Freeze>
    </ThemeContext.Provider>
  );
};

const Graphics = (props: ReelProps) => {
  const { fps } = useVideoConfig();
  return (
    <>
      {placedGraphics(props).map((g, i) => {
        const entry = GRAPHICS[g.type];
        if (!entry) throw new Error(`Unknown graphic type "${g.type}". Known: ${Object.keys(GRAPHICS).join(", ")}`);
        const from = Math.round(g.start * fps);
        const duration = Math.max(1, Math.round((g.end - g.start) * fps));
        const Component = entry.component;
        return (
          <Sequence key={i} from={from} durationInFrames={duration} layout="none">
            <GraphicFrame placement={g.placement} spec={g}>
              <Component {...(g.props ?? {})} />
            </GraphicFrame>
          </Sequence>
        );
      })}
    </>
  );
};

const HookLayer = (props: ReelProps) => {
  const { fps } = useVideoConfig();
  const hook = props.hook ?? props.title;
  const duration = Math.round((props.hookSeconds ?? props.titleSeconds ?? 2.8) * fps);
  if (!hook) return null;
  return (
    <Sequence durationInFrames={duration} layout="none">
      <Hook text={hook} duration={duration} />
    </Sequence>
  );
};

// The speaker. Every other jump cut is a hard punch-in, the way an editor hides a jump cut; a chapter
// change is a whip pan; a zoom is a slow push-in; and when paper slides up, the video lifts to keep the face in view.
const Speaker = (props: ReelProps & { sheets: GraphicSpec[] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const cuts = props.cuts ?? [];

  // cut.py gives each part its framing; older props just alternate at each cut.
  let scale = 1;
  if (props.style?.zoomCuts ?? true) {
    scale = props.framing
      ? (props.framing.filter((f) => f.at <= t + 1e-6).pop()?.scale ?? 1)
      : cuts.filter((c) => c <= t).length % 2 === 1 ? 1.06 : 1;
  }
  for (const z of props.zooms ?? []) {
    if (t >= z.start && t < z.end) scale *= 1 + 0.14 * Math.min(ease((t - z.start) / 1.2), ease((z.end - t) / 0.25));
  }

  // Whip pan: out to the left over the last 0.2 s before the chapter cut, in from the right after it.
  // The frame grows as it moves so no edge ever shows.
  let x = 0;
  let blur = 0;
  let whip = 0;
  for (const c of props.chapters ?? []) {
    const before = (c - t) / 0.2, after = (t - c) / 0.2;
    if (before > 0 && before <= 1) {
      const k = (1 - before) ** 2;
      x = -k * 320;
      blur = k * 36;
      whip = k;
    } else if (after >= 0 && after < 1) {
      const k = (1 - after) ** 2;
      x = k * 320;
      blur = k * 36;
      whip = k;
    }
  }

  const lift = Math.max(0, ...props.sheets.map((g) => presence(g, t))) * SHEET_LIFT;

  return (
    <AbsoluteFill style={{ transform: `translate(${x}px, ${-lift}px) scale(${1 + whip * 0.62})`, filter: blur ? `blur(${blur}px)` : undefined }}>
      <OffthreadVideo src={staticFile(props.video)} style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${scale})` }} />
    </AbsoluteFill>
  );
};

const GraphicFrame = ({ placement, spec, children }: { placement: Placement; spec: GraphicSpec; children: ReactNode }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const duration = Math.max(1, Math.round((spec.end - spec.start) * fps));
  const exit = useExit(duration, 7);
  const t = spec.start + frame / fps;

  if (placement === "over") {
    return (
      <AreaContext.Provider value={{ top: 0, bottom: 0, side: SAFE.side, kind: "over" }}>
        <AbsoluteFill style={{ opacity: exit, fontFamily: theme.font }}>{children}</AbsoluteFill>
      </AreaContext.Provider>
    );
  }

  const p = presence(spec, t);
  if (placement === "sheet") {
    const area: AreaBox = { top: SHEET_TOP + 64, bottom: SAFE.bottom - 40, side: 72, kind: "sheet" };
    return (
      <AreaContext.Provider value={area}>
        <AbsoluteFill style={{ transform: `translateY(${(1 - p) * (1920 - SHEET_TOP + 60)}px)` }}>
          <div style={{ position: "absolute", top: SHEET_TOP, left: 0, right: 0, bottom: -60, ...paperStyle(theme.paper), borderRadius: "44px 44px 0 0", boxShadow: "0 -20px 60px rgba(0,0,0,0.35)" }} />
          <div style={{ position: "absolute", top: SHEET_TOP + 22, left: "50%", width: 90, height: 8, marginLeft: -45, borderRadius: 4, background: `${theme.ink}22` }} />
          <AbsoluteFill style={{ opacity: clamp01(p * 1.6 - 0.4) }}>{children}</AbsoluteFill>
        </AbsoluteFill>
      </AreaContext.Provider>
    );
  }

  // Full: paper wipes up over everything, with a thin accent edge leading it.
  const area: AreaBox = { top: SAFE.top + 20, bottom: SAFE.bottom + 40, side: SAFE.side, kind: "full" };
  const cover = (1 - p) * 100;
  return (
    <AreaContext.Provider value={area}>
      <AbsoluteFill style={{ clipPath: `inset(${cover}% 0 0 0)` }}>
        <AbsoluteFill style={paperStyle(theme.paper)} />
        <AbsoluteFill style={{ opacity: clamp01(p * 1.6 - 0.4) }}>{children}</AbsoluteFill>
      </AbsoluteFill>
      {p > 0 && p < 1 && <div style={{ position: "absolute", left: 0, right: 0, top: `${cover}%`, height: 10, marginTop: -5, background: theme.accent }} />}
    </AreaContext.Provider>
  );
};

// The hook: a big serif headline in the open space above the speaker, word by word.
const Hook = ({ text, duration }: { text: string; duration: number }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const exit = useExit(duration, 8);
  const words = text.split(/(\*[^*]+\*|\s+)/g).filter((w) => w && !/^\s+$/.test(w));
  return (
    <AbsoluteFill style={{ padding: `${SAFE.top + 10}px ${SAFE.side}px 0`, opacity: exit, transform: `translateY(${(1 - exit) * -30}px)` }}>
      <div style={{ fontFamily: FONT_SERIF, fontSize: 128, lineHeight: 0.98, letterSpacing: -2, color: "#fff", textShadow: softShadow, display: "flex", flexWrap: "wrap", columnGap: 26 }}>
        {words.map((w, i) => {
          const k = ease((frame - i * 3) / 7);
          return (
            <span key={i} style={{ opacity: k, transform: `translateY(${(1 - k) * 24}px)`, display: "inline-block", filter: k < 1 ? `blur(${(1 - k) * 8}px)` : undefined }}>
              {w.startsWith("*") ? <Rich text={w} accent={theme.accent} /> : w}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

type Page = ReturnType<typeof createTikTokStyleCaptions>["pages"][number];

const Captions = ({ words, hidden, style }: { words: Word[]; hidden: (readonly [number, number])[]; style: Style }) => {
  const { fps } = useVideoConfig();
  const pages = useMemo(() => {
    const captions: Caption[] = words.map((w) => ({ text: ` ${w.text.trim()}`, startMs: w.start * 1000, endMs: w.end * 1000, timestampMs: null, confidence: null }));
    return createTikTokStyleCaptions({ captions, combineTokensWithinMilliseconds: style.wordsPerPageMs ?? 1100 }).pages;
  }, [words, style.wordsPerPageMs]);
  const emphasized = useMemo(() => new Set(words.filter((w) => w.emph).map((w) => Math.round(w.start * 1000))), [words]);

  return (
    <>
      {pages.map((page, i) => {
        const nextStart = pages[i + 1]?.startMs ?? page.startMs + page.durationMs;
        let end = Math.min(nextStart, page.startMs + page.durationMs + 400) / 1000;
        // A page that starts under a sheet or full graphic shows once it's gone, if it's still being said;
        // one that's showing when such a graphic comes in steps aside for it.
        let start = page.startMs / 1000;
        for (const [a, b] of hidden) if (start >= a && start < b) start = b;
        for (const [a] of hidden) if (a > start && a < end) end = a;
        if (end - start < 0.15) return null;
        return (
          <Sequence key={i} from={Math.round(start * fps)} durationInFrames={Math.max(1, Math.round((end - start) * fps))} layout="none">
            <CaptionPage page={page} style={style} offsetMs={start * 1000 - page.startMs} emphasized={emphasized} />
          </Sequence>
        );
      })}
    </>
  );
};

// Words appear as they're said; an emphasized one is set larger in the accent serif italic.
const CaptionPage = ({ page, style, offsetMs, emphasized }: { page: Page; style: Style; offsetMs: number; emphasized: Set<number> }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const nowMs = page.startMs + offsetMs + (frame / fps) * 1000;
  const size = style.captionSize ?? 62;
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 610 }}>
      <div style={{ fontFamily: theme.font, fontSize: size, fontWeight: 650, lineHeight: 1.12, textAlign: "center", maxWidth: 900, textShadow: softShadow, color: theme.text, textTransform: style.uppercase ? "uppercase" : "none" }}>
        {page.tokens.map((token, i) => {
          const k = ease((nowMs - token.fromMs) / 120);
          const emph = emphasized.has(Math.round(token.fromMs));
          return (
            <span key={i}>
              {i > 0 && " "}
              <span
                style={{
                  display: "inline-block",
                  opacity: k,
                  transform: `translateY(${(1 - k) * 10}px)`,
                  ...(emph ? { fontFamily: FONT_SERIF, fontStyle: "italic", fontWeight: 400, fontSize: size * 1.3, color: theme.accent, textTransform: "none" } : {}),
                }}
              >
                {token.text.trim()}
              </span>
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// Fine moving grain and a soft vignette, so footage and paper feel like one film.
const Grain = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(0,0,0,0.28) 100%)" }} />
      <svg width="1080" height="1920" style={{ position: "absolute", inset: 0, opacity: 0.08, mixBlendMode: "overlay" }}>
        <filter id={`grain${frame % 6}`}>
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed={frame % 6} stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="1080" height="1920" filter={`url(#grain${frame % 6})`} />
      </svg>
    </AbsoluteFill>
  );
};
