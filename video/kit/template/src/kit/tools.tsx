// The toolbox: parts with no look of their own. Every font, colour, size and position comes from the reel's design
// (design.md, props.style) or from the call, never from here. Build each reel's graphics from these; the example
// graphics in src/graphics/ show them in use, they're not a set to drop in.
//
//   Place      put anything at a spot inside the safe zones ("top", "upper", "middle", "lower", "left", "right") or at x/y
//   Text       text in any font (Google Fonts by name), any size, weight, colour, case, tracking, shadow, box behind it
//   Box        a shape: rectangle, pill or circle, filled, outlined or frosted (blur of what's behind)
//   Pic        a picture, screenshot or icon from the reel's folder
//   Clip       a crop of the reel's own video (a detail, a split screen, a picture-in-picture)
//   Mark       a highlight on words: marker, underline, box, circle, strike; drawn by hand or clean
//   Counter    a number that counts to its value
//   Bars, Ring, Line   simple charts that take their colours from the call
//
// Motion helpers are in ../anim.ts (enter, fade, rise, slide, wipe, blurIn, pop, stagger).
import { Box as RoughBox, Circle, Highlight, StrikeThrough, Underline } from "@remotion/rough-notation";
import { createContext, type CSSProperties, type ReactNode, useContext } from "react";
import { Img, interpolate, OffthreadVideo, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

import { loadFont, fontStack } from "../typefaces";
import { SAFE, softShadow, useTheme } from "../theme";

export const W = 1080, H = 1920;
// The area apps leave clear: top 250, bottom 480, right 140 (the like and share buttons), left 80.
export const ZONE = { top: SAFE.top, bottom: H - 480, left: SAFE.side, right: W - 140 } as const;

// Named spots inside the safe zones (the top edge of what's placed there).
export const SPOTS = { top: ZONE.top + 20, upper: 520, middle: 860, lower: 1130 } as const;
export type Spot = keyof typeof SPOTS;

export interface PlaceProps {
  at?: Spot; // a named spot, or give y
  x?: number; // px from the left (default: the safe zone's left edge)
  y?: number; // px from the top
  w?: number; // width (default: the safe zone's width from x)
  align?: "left" | "center" | "right";
  rotate?: number; // degrees
  style?: CSSProperties;
  children: ReactNode;
}

export const Place = ({ at = "upper", x, y, w, align = "left", rotate, style, children }: PlaceProps) => {
  const left = x ?? ZONE.left;
  return (
    <div
      style={{
        position: "absolute",
        left,
        top: y ?? SPOTS[at],
        width: w ?? ZONE.right - left,
        display: "flex",
        flexDirection: "column",
        alignItems: align === "left" ? "flex-start" : align === "right" ? "flex-end" : "center",
        textAlign: align,
        transform: rotate ? `rotate(${rotate}deg)` : undefined,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

export interface TextProps {
  children: ReactNode;
  font?: string; // any Google Fonts family, or the reel's ("body", "heading", "accent")
  size?: number;
  weight?: number;
  color?: string;
  italic?: boolean;
  uppercase?: boolean;
  tracking?: number; // letter spacing in px
  leading?: number; // line height
  shadow?: boolean | string; // true: a soft shadow for reading over video
  bg?: string; // a box behind the text
  pad?: number | string;
  radius?: number;
  maxWidth?: number;
  style?: CSSProperties;
}

const fontFor = (font: string | undefined, theme: ReturnType<typeof useTheme>) => {
  if (!font || font === "body") return theme.font;
  if (font === "heading") return theme.headingFont;
  if (font === "accent") return theme.accentFont;
  loadFont(font);
  return fontStack(font);
};

export const Text = ({ children, font, size = 64, weight, color, italic, uppercase, tracking, leading = 1.1, shadow, bg, pad, radius = 0, maxWidth, style }: TextProps) => {
  const theme = useTheme();
  return (
    <div
      style={{
        fontFamily: fontFor(font, theme),
        fontSize: size,
        fontWeight: weight ?? (font === "heading" ? theme.headingWeight : font === "accent" ? theme.accentWeight : 600),
        fontStyle: italic ? "italic" : "normal",
        color: color ?? theme.text,
        textTransform: uppercase ? "uppercase" : "none",
        letterSpacing: tracking,
        lineHeight: leading,
        textShadow: shadow === true ? softShadow : shadow || undefined,
        background: bg,
        padding: bg ? (pad ?? "0.12em 0.32em") : pad,
        borderRadius: radius,
        maxWidth,
        boxDecorationBreak: "clone",
        WebkitBoxDecorationBreak: "clone",
        ...style,
      }}
    >
      {children}
    </div>
  );
};

export interface BoxProps {
  x?: number;
  y?: number;
  w: number;
  h: number;
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
  radius?: number | "pill" | "circle";
  frost?: number; // blur what's behind it (px), for a glass panel over the video
  shadow?: string;
  style?: CSSProperties;
  children?: ReactNode;
}

export const Box = ({ x, y, w, h, fill = "transparent", stroke, strokeWidth = 3, radius = 0, frost, shadow, style, children }: BoxProps) => (
  <div
    style={{
      position: x === undefined && y === undefined ? "relative" : "absolute",
      left: x,
      top: y,
      width: w,
      height: h,
      background: fill,
      border: stroke ? `${strokeWidth}px solid ${stroke}` : undefined,
      borderRadius: radius === "pill" ? h / 2 : radius === "circle" ? "50%" : radius,
      backdropFilter: frost ? `blur(${frost}px)` : undefined,
      WebkitBackdropFilter: frost ? `blur(${frost}px)` : undefined,
      boxShadow: shadow,
      overflow: "hidden",
      ...style,
    }}
  >
    {children}
  </div>
);

export const Pic = ({ src, x, y, w, h, fit = "cover", radius = 0, shadow, style }: { src: string; x?: number; y?: number; w: number; h?: number; fit?: "cover" | "contain"; radius?: number; shadow?: string; style?: CSSProperties }) => (
  <Img
    src={staticFile(src)}
    style={{ position: x === undefined && y === undefined ? "relative" : "absolute", left: x, top: y, width: w, height: h, objectFit: fit, borderRadius: radius, boxShadow: shadow, display: "block", ...style }}
  />
);

// The reel's own video and where this graphic starts on it (GraphicFrame provides it), for Clip.
export const FootageContext = createContext<{ video: string; start: number }>({ video: "cut.mp4", start: 0 });

// A crop of the reel's own video: `focus` is the point to centre on (0 to 1 across and down) and how far to zoom in.
export const Clip = ({ x, y, w, h, focus = { x: 0.5, y: 0.4, zoom: 2 }, radius = 0, stroke, style }: { x?: number; y?: number; w: number; h: number; focus?: { x: number; y: number; zoom: number }; radius?: number | "circle"; stroke?: string; style?: CSSProperties }) => {
  const { video, start } = useContext(FootageContext);
  const { fps } = useVideoConfig();
  const scale = focus.zoom * Math.max(w / W, h / H);
  const vw = W * scale, vh = H * scale;
  return (
    <div style={{ position: x === undefined && y === undefined ? "relative" : "absolute", left: x, top: y, width: w, height: h, overflow: "hidden", borderRadius: radius === "circle" ? "50%" : radius, border: stroke ? `4px solid ${stroke}` : undefined, ...style }}>
      <OffthreadVideo
        src={staticFile(video)}
        startFrom={Math.round(start * fps)}
        muted
        style={{ position: "absolute", width: vw, height: vh, left: w / 2 - focus.x * vw, top: h / 2 - focus.y * vh, maxWidth: "none" }}
      />
    </div>
  );
};

// A highlight on words. `progress` 0 → 1 draws it (use anim's enter). Hand-drawn is one option, not the default.
export const Mark = ({ kind = "marker", hand = false, color, progress = 1, strokeWidth = 4, children }: { kind?: "marker" | "underline" | "box" | "circle" | "strike"; hand?: boolean; color?: string; progress?: number; strokeWidth?: number; children: ReactNode }) => {
  const theme = useTheme();
  const c = color ?? theme.accent;
  if (hand) {
    const Rough = { marker: Highlight, underline: Underline, box: RoughBox, circle: Circle, strike: StrikeThrough }[kind];
    return (
      <Rough progress={progress} color={c} strokeWidth={strokeWidth}>
        {children}
      </Rough>
    );
  }
  const p = Math.max(0, Math.min(1, progress));
  const line = (top: string, height: number | string) => (
    <span style={{ position: "absolute", left: 0, top, height, width: `${p * 100}%`, background: c, zIndex: -1 }} />
  );
  return (
    <span style={{ position: "relative", display: "inline-block", isolation: "isolate", padding: kind === "box" || kind === "circle" ? "0 0.15em" : undefined }}>
      {kind === "marker" && line("0.12em", "0.86em")}
      {kind === "underline" && line("1.02em", strokeWidth)}
      {kind === "strike" && <span style={{ position: "absolute", left: 0, top: "52%", height: strokeWidth, width: `${p * 100}%`, background: c }} />}
      {(kind === "box" || kind === "circle") && (
        <span style={{ position: "absolute", inset: "-0.06em -0.04em", border: `${strokeWidth}px solid ${c}`, borderRadius: kind === "circle" ? "50%" : 6, opacity: p, transform: `scale(${0.9 + 0.1 * p})` }} />
      )}
      {children}
    </span>
  );
};

// A number counting to `to` over `seconds`, from the start of its graphic (or `delay` seconds in).
export const Counter = ({ to, from = 0, seconds = 0.9, delay = 0, decimals = 0, prefix = "", suffix = "", locale = "en-US" }: { to: number; from?: number; seconds?: number; delay?: number; decimals?: number; prefix?: string; suffix?: string; locale?: string }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const v = interpolate(frame, [delay * fps, (delay + seconds) * fps], [from, to], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (t) => 1 - (1 - t) ** 3 });
  return (
    <span style={{ fontVariantNumeric: "tabular-nums" }}>
      {prefix}
      {v.toLocaleString(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}
      {suffix}
    </span>
  );
};

// Bars growing to their values. Colours, fonts and sizes come from the call (or the reel's theme).
export const Bars = ({ data, w = 840, barHeight = 46, gap = 22, color, highlight, track = "transparent", textColor, font, size = 40, delay = 0 }: { data: { label: string; value: number }[]; w?: number; barHeight?: number; gap?: number; color?: string; highlight?: string; track?: string; textColor?: string; font?: string; size?: number; delay?: number }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div style={{ width: w, display: "flex", flexDirection: "column", gap, fontFamily: fontFor(font, theme), color: textColor ?? theme.text, fontSize: size, fontWeight: 600 }}>
      {data.map((d, i) => {
        const grow = interpolate(frame - (delay * fps + i * 5), [0, fps * 0.7], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (t) => 1 - (1 - t) ** 3 });
        const top = d.value === max;
        return (
          <div key={i}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span>{d.label}</span>
              <span>{Math.round(d.value * grow).toLocaleString("en-US")}</span>
            </div>
            <div style={{ height: barHeight, background: track }}>
              <div style={{ height: "100%", width: `${(d.value / max) * 100 * grow}%`, background: top && highlight ? highlight : color ?? theme.accent }} />
            </div>
          </div>
        );
      })}
    </div>
  );
};

// A ring filling to `value` (0 to 1), with anything in the middle.
export const Ring = ({ value, size = 320, thickness = 24, color, track = "rgba(255,255,255,0.18)", delay = 0, children }: { value: number; size?: number; thickness?: number; color?: string; track?: string; delay?: number; children?: ReactNode }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = interpolate(frame, [delay * fps, (delay + 1) * fps], [0, value], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (t) => 1 - (1 - t) ** 3 });
  const r = (size - thickness) / 2, c = 2 * Math.PI * r;
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={thickness} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color ?? theme.accent} strokeWidth={thickness} strokeDasharray={`${c * p} ${c}`} />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>{children}</div>
    </div>
  );
};

// A line drawing itself through `points` (any scale; it fits them to w × h).
export const Line = ({ points, w = 840, h = 360, color, strokeWidth = 8, delay = 0, dots = false }: { points: number[]; w?: number; h?: number; color?: string; strokeWidth?: number; delay?: number; dots?: boolean }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = interpolate(frame, [delay * fps, (delay + 1.2) * fps], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const lo = Math.min(...points), hi = Math.max(...points), span = hi - lo || 1;
  const xy = points.map((v, i) => [(i / Math.max(1, points.length - 1)) * w, h - ((v - lo) / span) * h] as const);
  const d = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const length = xy.reduce((sum, [x, y], i) => (i ? sum + Math.hypot(x - xy[i - 1][0], y - xy[i - 1][1]) : 0), 0);
  return (
    <svg width={w} height={h} style={{ overflow: "visible" }}>
      <path d={d} fill="none" stroke={color ?? theme.accent} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={length} strokeDashoffset={length * (1 - p)} />
      {dots && xy.map(([x, y], i) => i / (xy.length - 1) <= p && <circle key={i} cx={x} cy={y} r={strokeWidth * 1.2} fill={color ?? theme.accent} />)}
    </svg>
  );
};
