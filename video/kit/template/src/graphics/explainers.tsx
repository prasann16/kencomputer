// Graphics that explain an idea by moving, for the one or two moments in a reel that carry it.
// Same paper look as basics.tsx; each one does a single thing.
import { Circle, CrossedOff } from "@remotion/rough-notation";
import { interpolate, useCurrentFrame, useVideoConfig } from "remotion";

import { pop, rise, useEnter } from "../anim";
import { SAFE, useTheme } from "../theme";
import { Area, FONT_SERIF, Heading, paperStyle, Rich } from "./layout";

const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - (1 - x) ** 3);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

// Small apps get swallowed one by one by a few big players.
// { title?, eaters: string[] (2 to 3), apps: [{label, icon?}] (4 to 6) }
export const Eaten = ({ title, eaters, apps }: { title?: string; eaters: string[]; apps: { label: string; icon?: string }[] }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const width = 936;
  const tile = Math.min(132, (width - (apps.length - 1) * 18) / apps.length);
  const gap = (width - tile * apps.length) / Math.max(1, apps.length - 1);
  const blob = 150;
  const blobGap = (width - blob * eaters.length) / Math.max(1, eaters.length - 1);
  const firstBite = fps * 1.2;
  const step = fps * 0.75;
  const flight = fps * 0.5;
  const tileTop = 0;
  const blobTop = tile + 120;

  return (
    <Area>
      {title && (
        <Heading size={78} style={rise(useEnter(0))}>
          <Rich text={title} />
        </Heading>
      )}
      <div style={{ position: "relative", width, height: blobTop + blob + 50 }}>
        {apps.map((app, i) => {
          const eater = i % eaters.length;
          const k = ease((frame - firstBite - i * step) / flight);
          const x0 = i * (tile + gap), y0 = tileTop;
          const x1 = eater * (blob + blobGap) + blob / 2 - tile / 2, y1 = blobTop + blob / 2 - tile / 2;
          const appear = ease((frame - 4 - i * 2) / 8);
          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: interpolate(k, [0, 1], [x0, x1]),
                top: interpolate(k, [0, 1], [y0, y1]),
                width: tile,
                height: tile,
                borderRadius: tile * 0.24,
                background: "#FFFFFF",
                border: `2px solid ${theme.ink}22`,
                boxShadow: "0 6px 16px rgba(40,30,10,0.12)",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                opacity: appear * (1 - k),
                transform: `scale(${appear * (1 - 0.8 * k)}) rotate(${k * 90}deg)`,
              }}
            >
              <div style={{ fontSize: tile * 0.34, fontWeight: 700, lineHeight: 1, color: theme.ink }}>{app.icon ?? app.label[0]}</div>
              <div style={{ fontSize: tile * 0.15, fontWeight: 600, color: `${theme.ink}aa` }}>{app.label}</div>
            </div>
          );
        })}
        {eaters.map((name, j) => {
          // Each bite makes it a little bigger, with a quick gulp.
          const bites = apps.map((_, i) => i).filter((i) => i % eaters.length === j);
          const eaten = bites.reduce((n, i) => n + ease((frame - firstBite - i * step - flight) / 6), 0);
          const gulp = bites.reduce((g, i) => {
            const t = frame - firstBite - i * step - flight;
            return g + (t > 0 && t < 8 ? Math.sin((t / 8) * Math.PI) * 0.12 : 0);
          }, 0);
          const enter = ease((frame - 6 - j * 3) / 10);
          const scale = enter * (0.8 + eaten * 0.16 + gulp);
          return (
            <div key={j} style={{ position: "absolute", left: j * (blob + blobGap), top: blobTop, width: blob, display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div style={{ width: blob, height: blob, borderRadius: "50%", background: j === 0 ? theme.accent : theme.ink, transform: `scale(${scale})`, boxShadow: "0 10px 30px rgba(40,30,10,0.25)" }} />
              <div style={{ marginTop: 18, fontSize: 34, fontWeight: 650, color: theme.ink, opacity: enter, whiteSpace: "nowrap" }}>{name}</div>
            </div>
          );
        })}
      </div>
    </Area>
  );
};

// Jobs crossed out one after another. { title?, items: string[] (3 to 4) }
export const Obsolete = ({ title, items }: { title?: string; items: string[] }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const step = Math.round(fps * 0.45);
  return (
    <Area>
      {title && (
        <Heading size={78} style={rise(useEnter(0))}>
          <Rich text={title} />
        </Heading>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {items.map((item, i) => {
          const enter = ease((frame - 3 - i * 3) / 8);
          const strike = interpolate(frame, [10 + i * step, 10 + i * step + 9], [0, 1], clamp);
          return (
            <div key={i} style={{ ...pop(enter, 0.9), background: "#FFFFFF", border: `2px solid ${theme.ink}1f`, borderRadius: 22, padding: "26px 28px", fontSize: 44, fontWeight: 650, color: theme.ink, opacity: enter * (1 - strike * 0.45) }}>
              <CrossedOff progress={strike} color={theme.accent} strokeWidth={5}>
                {item}
              </CrossedOff>
            </div>
          );
        })}
      </div>
    </Area>
  );
};

// A huge do-everything blob next to one small, sharp thing. { big, small, label }
export const Focus = ({ big, small, label }: { big: string; small: string; label: string }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  // A slow wobble, so the big one feels shapeless.
  const r = (k: number) => 46 + 6 * Math.sin(t * 2.1 + k) + 4 * Math.sin(t * 3.3 + k * 2);
  const blobEnter = ease(frame / 12);
  const smallEnter = ease((frame - fps * 0.9) / 10);
  const ring = interpolate(frame, [fps * 1.3, fps * 1.3 + 14], [0, 1], clamp);
  return (
    <Area style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 30 }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18, opacity: blobEnter }}>
        <div
          style={{
            width: 400,
            height: 340,
            background: `${theme.ink}16`,
            borderRadius: `${r(0)}% ${100 - r(1)}% ${r(2)}% ${100 - r(3)}% / ${r(4)}% ${r(5)}% ${100 - r(6)}% ${100 - r(7)}%`,
            transform: `scale(${0.85 + 0.15 * blobEnter})`,
            display: "grid",
            placeItems: "center",
            fontSize: 38,
            fontWeight: 650,
            color: `${theme.ink}88`,
            textAlign: "center",
            padding: 40,
          }}
        >
          {big}
        </div>
      </div>
      <div style={{ fontFamily: FONT_SERIF, fontSize: 64, color: `${theme.ink}66`, opacity: smallEnter }}>vs</div>
      <div style={{ ...pop(smallEnter, 0.6), display: "flex", flexDirection: "column", alignItems: "center", gap: 22, width: 330 }}>
        <Circle progress={ring} color={theme.accent} strokeWidth={4} padding={{ top: 18, bottom: 18, left: 18, right: 18 }}>
          <div style={{ width: 110, height: 110, background: theme.accent, transform: "rotate(45deg)", borderRadius: 14, boxShadow: "0 10px 26px rgba(40,30,10,0.25)" }} />
        </Circle>
        <div style={{ fontFamily: FONT_SERIF, fontSize: 64, lineHeight: 1, color: theme.ink }}>{small}</div>
        <div style={{ fontSize: 34, fontWeight: 600, lineHeight: 1.2, textAlign: "center", color: theme.ink }}>
          <Rich text={label} />
        </div>
      </div>
    </Area>
  );
};

// A browser bar that types out an address, in the open space above their head. { url }
export const Browser = ({ url }: { url: string }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const enter = useEnter(0, 12);
  const typed = Math.floor(interpolate(frame, [8, 8 + url.length * 1.6], [0, url.length], clamp));
  const caret = typed < url.length || Math.floor(frame / 8) % 2 === 0;
  const done = ease((frame - 10 - url.length * 1.6) / 6);
  return (
    <div style={{ position: "absolute", top: SAFE.top + 40, left: SAFE.side, right: SAFE.side, display: "flex", justifyContent: "center" }}>
      <div style={{ ...rise(enter, 30), ...paperStyle("#FFFFFF"), width: 860, borderRadius: 28, boxShadow: "0 20px 50px rgba(0,0,0,0.35)", overflow: "hidden" }}>
        <div style={{ display: "flex", gap: 12, padding: "20px 26px 0" }}>
          {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => (
            <div key={c} style={{ width: 20, height: 20, borderRadius: 10, background: c }} />
          ))}
        </div>
        <div style={{ margin: "18px 22px 24px", padding: "18px 26px", borderRadius: 18, background: "#F1EEE7", display: "flex", alignItems: "center", gap: 16, transform: `scale(${1 + Math.sin(done * Math.PI) * 0.03})` }}>
          <span style={{ fontSize: 34, opacity: 0.45 }}>⌕</span>
          <span style={{ fontSize: 48, fontWeight: 600, color: theme.ink, letterSpacing: -0.5 }}>
            {url.slice(0, typed)}
            <span style={{ opacity: caret ? 1 : 0, color: theme.accent }}>|</span>
          </span>
        </div>
      </div>
    </div>
  );
};

// Something goes in, gets worked on, comes out finished: a long raw clip → a worker → a short result,
// with a timer counting how long it took. { title?, input, worker, output, time? }
export const Flow = ({ title, input, worker, output, time }: { title?: string; input: string; worker: string; output: string; time?: string }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const inEnter = ease((frame - 4) / 10);
  // The clip slides into the worker, the worker spins while it works, then the result pops out.
  const feed = ease((frame - fps * 0.9) / (fps * 0.6));
  const work = interpolate(frame, [fps * 1.4, fps * 2.4], [0, 1], clamp);
  const outEnter = ease((frame - fps * 2.4) / 10);
  const timerEnter = ease((frame - fps * 1.4) / 8);
  const box = 230;
  const card = { background: "#FFFFFF", border: `2px solid ${theme.ink}1f`, borderRadius: 24, boxShadow: "0 8px 20px rgba(40,30,10,0.12)" } as const;
  const label = { marginTop: 18, fontSize: 36, fontWeight: 650, color: theme.ink, textAlign: "center" as const, whiteSpace: "nowrap" as const };
  const arrow = (k: number) => (
    <svg width={80} height={40} style={{ opacity: k, flexShrink: 0 }}>
      <path d={`M4 20 H${4 + 64 * k}`} stroke={theme.ink} strokeWidth={4} strokeLinecap="round" />
      {k > 0.9 && <path d="M58 8 L72 20 L58 32" fill="none" stroke={theme.ink} strokeWidth={4} strokeLinecap="round" strokeLinejoin="round" />}
    </svg>
  );
  return (
    <Area>
      {title && (
        <Heading size={78} style={rise(useEnter(0))}>
          <Rich text={title} />
        </Heading>
      )}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", opacity: inEnter }}>
          {/* A long, messy strip of footage */}
          <div style={{ ...card, width: box, height: box, display: "flex", flexDirection: "column", justifyContent: "center", gap: 10, padding: 22, transform: `translateX(${feed * 40}px) scale(${1 - feed * 0.12})`, opacity: 1 - feed * 0.5 }}>
            {[0.9, 0.55, 0.8, 0.4, 0.7].map((w, i) => (
              <div key={i} style={{ height: 16, width: `${w * 100}%`, borderRadius: 8, background: `${theme.ink}${i % 2 ? "26" : "40"}` }} />
            ))}
          </div>
          <div style={label}>{input}</div>
        </div>
        {arrow(ease((frame - fps * 0.6) / 8))}
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", opacity: ease((frame - 8) / 10) }}>
          <div style={{ width: box, height: box, borderRadius: "50%", background: theme.accent, display: "grid", placeItems: "center", boxShadow: "0 12px 30px rgba(40,30,10,0.25)", transform: `scale(${1 + Math.sin(work * Math.PI) * 0.08})`, position: "relative" }}>
            <svg width={box} height={box} style={{ position: "absolute", inset: 0, transform: `rotate(${work * 540}deg)`, opacity: work > 0 && work < 1 ? 1 : 0 }}>
              <circle cx={box / 2} cy={box / 2} r={box / 2 - 12} fill="none" stroke="#FFFFFF" strokeWidth={6} strokeDasharray="60 500" strokeLinecap="round" />
            </svg>
            <div style={{ fontFamily: FONT_SERIF, fontSize: 76, color: "#FFFFFF" }}>{worker}</div>
          </div>
          <div style={{ ...label, opacity: timerEnter, color: theme.accent }}>{time ?? ""}</div>
        </div>
        {arrow(ease((frame - fps * 2.2) / 8))}
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", ...pop(outEnter, 0.6) }}>
          {/* A finished vertical reel */}
          <div style={{ ...card, width: box * 0.62, height: box, borderRadius: 26, background: theme.ink, overflow: "hidden", position: "relative" }}>
            <div style={{ position: "absolute", left: 16, right: 16, bottom: 26, height: 14, borderRadius: 7, background: "#FFFFFF" }} />
            <div style={{ position: "absolute", left: 30, right: 30, bottom: 50, height: 14, borderRadius: 7, background: theme.accent }} />
            <div style={{ position: "absolute", top: "34%", left: "50%", transform: "translate(-40%, -50%)", width: 0, height: 0, borderTop: "22px solid transparent", borderBottom: "22px solid transparent", borderLeft: "36px solid #FFFFFF" }} />
          </div>
          <div style={label}>{output}</div>
        </div>
      </div>
    </Area>
  );
};

// The manual way, shown as an editing timeline (tracks, clips, keyframes) that gets crossed out.
// { title?, label? }
export const Timeline = ({ title, label }: { title?: string; label?: string }) => {
  const theme = useTheme();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = ease((frame - 3) / 10);
  const strike = interpolate(frame, [fps * 1.3, fps * 1.3 + 12], [0, 1], clamp);
  const head = interpolate(frame, [6, fps * 1.4], [40, 700], clamp);
  const tracks = [
    { color: "#7C9CF0", clips: [[0, 180], [195, 420], [440, 600], [610, 820]] },
    { color: "#F0B35A", clips: [[60, 260], [300, 380], [520, 760]] },
    { color: "#8CCB8E", clips: [[0, 120], [140, 500], [540, 840]] },
    { color: "#E58FB4", clips: [[90, 160], [250, 330], [400, 470], [690, 760]] },
  ];
  return (
    <Area>
      {title && (
        <Heading size={78} style={rise(useEnter(0))}>
          <Rich text={title} />
        </Heading>
      )}
      <CrossedOff progress={strike} color={theme.accent} strokeWidth={7}>
        <div style={{ ...pop(enter, 0.92), position: "relative", width: 900, boxSizing: "border-box", background: "#2A2724", borderRadius: 22, padding: "26px 24px", boxShadow: "0 14px 34px rgba(40,30,10,0.3)", opacity: enter * (1 - strike * 0.35) }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {tracks.map((t, i) => (
              <div key={i} style={{ position: "relative", height: 54, background: "#3A3632", borderRadius: 8 }}>
                {t.clips.map(([a, b], j) => (
                  <div key={j} style={{ position: "absolute", left: a, width: b - a - 6, top: 6, bottom: 6, borderRadius: 6, background: t.color, opacity: 0.9 }}>
                    {j % 2 === 0 && <div style={{ position: "absolute", left: 14, top: 14, width: 12, height: 12, background: "#FFFFFF", transform: "rotate(45deg)" }} />}
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div style={{ position: "absolute", top: 12, bottom: 12, left: 24 + head, width: 4, background: "#FF4D4D", borderRadius: 2 }} />
        </div>
      </CrossedOff>
      {label && (
        <div style={{ marginTop: 26, fontSize: 44, fontWeight: 650, color: theme.ink, textAlign: "center", opacity: ease((frame - fps * 1.6) / 8) }}>
          <Rich text={label} />
        </div>
      )}
    </Area>
  );
};
