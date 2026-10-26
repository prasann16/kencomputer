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
