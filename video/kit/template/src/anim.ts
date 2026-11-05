import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

// 0 → 1 as an element enters, starting `delay` frames into its Sequence.
export function useEnter(delay = 0, durationInFrames = 14): number {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - delay, fps, durationInFrames, config: { damping: 200 } });
}

// 1 → 0 over the last `frames` of a Sequence that lasts `durationInFrames`, so graphics leave instead of vanishing.
// (useVideoConfig() inside a Sequence reports the whole video's length, so pass the Sequence's own.)
export function useExit(durationInFrames: number, frames = 8): number {
  const frame = useCurrentFrame();
  return interpolate(frame, [durationInFrames - frames, durationInFrames], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
}

export const rise = (enter: number, distance = 40) => ({
  opacity: enter,
  transform: `translateY(${interpolate(enter, [0, 1], [distance, 0])}px)`,
});

export const pop = (enter: number, from = 0.85) => ({
  opacity: enter,
  transform: `scale(${interpolate(enter, [0, 1], [from, 1])})`,
});

// More ways in, each from an `enter` value (0 → 1). Pick per reel; none is the house move.
export const fade = (enter: number) => ({ opacity: enter });

export const slide = (enter: number, from: "left" | "right" | "up" | "down" = "left", distance = 120) => {
  const d = interpolate(enter, [0, 1], [distance, 0]);
  const [x, y] = { left: [-d, 0], right: [d, 0], up: [0, -d], down: [0, d] }[from];
  return { opacity: Math.min(1, enter * 1.5), transform: `translate(${x}px, ${y}px)` };
};

// Revealed by a moving edge (no fade): from the left, right, top or bottom.
export const wipe = (enter: number, from: "left" | "right" | "up" | "down" = "left") => {
  const rest = (1 - Math.max(0, Math.min(1, enter))) * 100;
  const inset = { left: `0 ${rest}% 0 0`, right: `0 0 0 ${rest}%`, up: `0 0 ${rest}% 0`, down: `${rest}% 0 0 0` }[from];
  return { clipPath: `inset(${inset})` };
};

export const blurIn = (enter: number, blur = 16) => ({ opacity: enter, filter: enter < 1 ? `blur(${(1 - enter) * blur}px)` : undefined });

// The delay (in frames) of item i when items come in one after another.
export const stagger = (i: number, step = 5, first = 0) => first + i * step;

// An entrance by name, for graphics described in props.json.
export type Entrance = "fade" | "rise" | "pop" | "blur" | "slide-left" | "slide-right" | "slide-up" | "slide-down" | "wipe-left" | "wipe-right" | "wipe-up" | "wipe-down" | "none";
export const entrance = (name: Entrance, enter: number) => {
  if (name === "none") return {};
  if (name === "fade") return fade(enter);
  if (name === "rise") return rise(enter);
  if (name === "pop") return pop(enter);
  if (name === "blur") return blurIn(enter);
  const [kind, dir] = name.split("-") as ["slide" | "wipe", "left" | "right" | "up" | "down"];
  return kind === "slide" ? slide(enter, dir) : wipe(enter, dir);
};
