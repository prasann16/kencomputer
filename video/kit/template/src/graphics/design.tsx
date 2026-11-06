// "design": a graphic made of toolbox parts, described entirely in props.json, so a reel can have its own graphics
// without writing code. Each item is placed, styled and animated by the reel's design; nothing here has a look.
//
// { "type": "design", "start": 8.4, "end": 11.6, "props": { "items": [
//     { "kind": "box", "x": 80, "y": 1080, "w": 860, "h": 260, "fill": "#0B3D2Ecc", "radius": 28, "enter": "wipe-up" },
//     { "kind": "text", "text": "Rent went up *41%*", "at": "lower", "y": 1120, "font": "Bricolage Grotesque",
//       "size": 96, "weight": 800, "color": "#F4EBD0", "enter": "slide-left", "delay": 0.15 },
//     { "kind": "counter", "to": 41, "suffix": "%", "y": 600, "font": "DM Mono", "size": 220, "color": "#FFB000" },
//     { "kind": "pic", "src": "logo.png", "x": 760, "y": 300, "w": 180 },
//     { "kind": "clip", "x": 600, "y": 980, "w": 380, "h": 380, "focus": {"x": 0.5, "y": 0.62, "zoom": 2.4}, "radius": 190 }
// ] } }
//
// Common to every item: at / x / y / w / align (where), enter (an anim.ts entrance, default "fade"), delay (seconds
// after the graphic starts), exitBefore (seconds before the graphic ends that it leaves, default 0).
// text: text (*starred* words in mark style), font, size, weight, color, italic, uppercase, tracking, leading,
//   shadow, bg, pad, radius, maxWidth, mark ("marker" | "underline" | "box" | "circle" | "strike"), markColor, hand
// box: w, h, fill, stroke, strokeWidth, radius ("pill" | "circle" | px), frost, shadow
// pic: src, w, h, fit, radius, shadow · clip: w, h, focus, radius, stroke
// counter: to, from, prefix, suffix, decimals, seconds, and text's style props
// bars: data [{label, value}], color, highlight, track, textColor, font, size, w · ring: value, size, thickness,
//   color, track, label (text in the middle) · line: points, w, h, color, strokeWidth, dots
import { interpolate, useCurrentFrame, useVideoConfig } from "remotion";

import { type Entrance, entrance, useEnter } from "../anim";
import { Bars, Box, Clip, Counter, Line, Mark, Pic, Place, Ring, type Spot, Text } from "../kit/tools";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Item = Record<string, any> & { kind: string; enter?: Entrance; delay?: number; exitBefore?: number };

const Starred = ({ text, mark, markColor, hand, progress }: { text: string; mark?: "marker" | "underline" | "box" | "circle" | "strike"; markColor?: string; hand?: boolean; progress: number }) => (
  <>
    {text.split(/(\*[^*]+\*)/g).filter(Boolean).map((part, i) =>
      part.startsWith("*") && part.endsWith("*") ? (
        mark ? (
          <Mark key={i} kind={mark} color={markColor} hand={hand} progress={progress}>
            {part.slice(1, -1)}
          </Mark>
        ) : (
          <span key={i} style={{ color: markColor }}>{part.slice(1, -1)}</span>
        )
      ) : (
        <span key={i}>{part}</span>
      ),
    )}
  </>
);

const DesignItem = ({ item, duration }: { item: Item; duration: number }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const delay = Math.round((item.delay ?? 0) * fps);
  const enter = useEnter(delay, 14);
  const leaveAt = duration - Math.round((item.exitBefore ?? 0) * fps);
  const leave = item.exitBefore ? interpolate(frame, [leaveAt - 8, leaveAt], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 1;
  const markProgress = interpolate(frame, [delay + 10, delay + 24], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const motion = entrance(item.enter ?? "fade", enter);
  const textStyle = { font: item.font, size: item.size, weight: item.weight, color: item.color, italic: item.italic, uppercase: item.uppercase, tracking: item.tracking, leading: item.leading, shadow: item.shadow, bg: item.bg, pad: item.pad, radius: item.radius, maxWidth: item.maxWidth };

  let body;
  switch (item.kind) {
    case "text":
      body = <Text {...textStyle}><Starred text={String(item.text ?? "")} mark={item.mark} markColor={item.markColor} hand={item.hand} progress={markProgress} /></Text>;
      break;
    case "counter":
      body = <Text {...textStyle}><Counter to={item.to} from={item.from} prefix={item.prefix} suffix={item.suffix} decimals={item.decimals} seconds={item.seconds} delay={item.delay} /></Text>;
      break;
    case "box":
      body = <Box w={item.w} h={item.h} fill={item.fill} stroke={item.stroke} strokeWidth={item.strokeWidth} radius={item.radius} frost={item.frost} shadow={item.shadow} />;
      break;
    case "pic":
      body = <Pic src={item.src} w={item.w} h={item.h} fit={item.fit} radius={item.radius} shadow={item.shadow} />;
      break;
    case "clip":
      body = <Clip w={item.w} h={item.h} focus={item.focus} radius={item.radius} stroke={item.stroke} />;
      break;
    case "bars":
      body = <Bars data={item.data} w={item.w} color={item.color} highlight={item.highlight} track={item.track} textColor={item.textColor} font={item.font} size={item.size} delay={item.delay} />;
      break;
    case "ring":
      body = <Ring value={item.value} size={item.size} thickness={item.thickness} color={item.color} track={item.track} delay={item.delay}>{item.label && <Text {...textStyle} size={item.labelSize ?? 72}>{item.label}</Text>}</Ring>;
      break;
    case "line":
      body = <Line points={item.points} w={item.w} h={item.h} color={item.color} strokeWidth={item.strokeWidth} dots={item.dots} delay={item.delay} />;
      break;
    default:
      throw new Error(`design: unknown item kind "${item.kind}" (text, counter, box, pic, clip, bars, ring, line)`);
  }
  const w = ["box", "pic", "clip"].includes(item.kind) ? item.w : item.wrap;
  return (
    <Place at={(item.at ?? "upper") as Spot} x={item.x} y={item.y} w={w} align={item.align} rotate={item.rotate}>
      <div style={{ ...motion, opacity: (motion as { opacity?: number }).opacity === undefined ? leave : (motion as { opacity: number }).opacity * leave }}>{body}</div>
    </Place>
  );
};

export const Design = ({ items, durationSeconds }: { items: Item[]; durationSeconds?: number }) => {
  const { fps } = useVideoConfig();
  const duration = Math.round((durationSeconds ?? 1e6) * fps);
  return (
    <>
      {items.map((item, i) => (
        <DesignItem key={i} item={item} duration={duration} />
      ))}
    </>
  );
};
