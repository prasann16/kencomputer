// The standard graphics. Each takes its own props from graphics[].props in props.json.
// Sheet and full graphics sit on paper (ink text, serif headlines, hand-drawn marks); "over" ones float on the video.
// In any text prop, *starred words* are set in the accent serif italic.
import { Circle, Highlight, StrikeThrough, Underline } from "@remotion/rough-notation";
import { Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

import { pop, rise, useEnter } from "../anim";
import { SAFE, softShadow, useTheme } from "../theme";
import { Area, FONT_SERIF, Heading, Kicker, paperStyle, Rich, useArea } from "./layout";

// 0 → 1 over `frames`, starting `delay` frames in: how far a hand-drawn mark has been drawn.
const useDraw = (delay: number, frames = 14) => {
  const frame = useCurrentFrame();
  return interpolate(frame, [delay, delay + frames], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
};

// A section opener on full paper. { kicker?, text }
export const Title = ({ kicker, text }: { kicker?: string; text: string }) => (
  <Area style={{ justifyContent: "center" }}>
    {kicker && <Kicker style={rise(useEnter(0))}>{kicker}</Kicker>}
    <Heading size={128} style={rise(useEnter(4), 50)}>
      <Rich text={text} />
    </Heading>
  </Area>
);

// One number that matters. { value, prefix?, suffix?, label, note? } A numeric value counts up.
export const Stat = ({ value, prefix = "", suffix = "", label, note }: { value: number | string; prefix?: string; suffix?: string; label: string; note?: string }) => {
  const theme = useTheme();
  const area = useArea();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const shown =
    typeof value === "number"
      ? Math.round(interpolate(frame, [0, fps * 0.9], [0, value], { extrapolateRight: "clamp", easing: (t) => 1 - (1 - t) ** 3 })).toLocaleString("en-US")
      : value;
  const big = area.kind === "sheet" ? 240 : 300;
  return (
    <Area>
      <div style={{ ...pop(useEnter(0), 0.8), fontFamily: FONT_SERIF, fontSize: big, lineHeight: 0.9, color: theme.accent, letterSpacing: -4 }}>
        {prefix}
        {shown}
        {suffix}
      </div>
      <div style={{ ...rise(useEnter(6)), fontSize: area.kind === "sheet" ? 54 : 60, fontWeight: 600, lineHeight: 1.15, alignSelf: "flex-start" }}>
        <Underline progress={useDraw(14)} color={theme.accent} strokeWidth={4}>
          <Rich text={label} />
        </Underline>
      </div>
      {note && <div style={{ ...rise(useEnter(12)), fontSize: 32, opacity: 0.6 }}>{note}</div>}
    </Area>
  );
};

// Points revealed one by one. { title?, items: string[], marker?: "numbers" | "checks" | "bullets" }
export const List = ({ title, items, marker = "numbers" }: { title?: string; items: string[]; marker?: "numbers" | "checks" | "bullets" }) => {
  const theme = useTheme();
  const area = useArea();
  const { fps } = useVideoConfig();
  // Spread the reveals over the first ~60% of the graphic.
  const step = Math.max(6, Math.round((fps * 1.6) / Math.max(1, items.length)));
  const size = area.kind === "sheet" ? 54 : 62;
  return (
    <Area>
      {title && (
        <Heading style={rise(useEnter(0))}>
          <Rich text={title} />
        </Heading>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: area.kind === "sheet" ? 14 : 26 }}>
        {items.map((item, i) => (
          <ListRow key={i} delay={6 + i * step} size={size} accent={theme.accent} mark={marker === "numbers" ? String(i + 1) : marker === "checks" ? "✓" : "•"}>
            {item}
          </ListRow>
        ))}
      </div>
    </Area>
  );
};

const ListRow = ({ delay, size, accent, mark, children }: { delay: number; size: number; accent: string; mark: string; children: string }) => {
  const enter = useEnter(delay);
  return (
    <div style={{ ...rise(enter, 24), display: "flex", gap: 26, alignItems: "baseline" }}>
      <div style={{ flex: "none", width: size * 1.1, textAlign: "center", fontFamily: FONT_SERIF, fontStyle: "italic", fontSize: size * 1.25, color: accent }}>
        <Circle progress={useDraw(delay + 4, 12)} color={accent} strokeWidth={3} padding={{ top: 2, bottom: 2, left: 8, right: 8 }}>
          {mark}
        </Circle>
      </div>
      <div style={{ fontSize: size, fontWeight: 600, lineHeight: 1.15 }}>
        <Rich text={children} />
      </div>
    </div>
  );
};

// Old way crossed out, new way highlighted. { title?, left: {label, items}, right: {label, items} }
export const Compare = ({ title, left, right }: { title?: string; left: { label: string; items: string[] }; right: { label: string; items: string[] } }) => {
  const theme = useTheme();
  const area = useArea();
  const size = area.kind === "sheet" ? 46 : 54;
  const column = (side: { label: string; items: string[] }, isNew: boolean, delay: number) => (
    <div style={{ ...rise(useEnter(delay)), flex: 1, display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ fontFamily: FONT_SERIF, fontStyle: isNew ? "italic" : "normal", fontSize: size * 1.6, color: isNew ? theme.accent : theme.ink, opacity: isNew ? 1 : 0.55 }}>{side.label}</div>
      {side.items.map((item, i) => (
        <div key={i} style={{ fontSize: size, fontWeight: 600, lineHeight: 1.2, opacity: isNew ? 1 : 0.55 }}>
          {isNew ? (
            <Highlight progress={useDraw(delay + 10 + i * 6)} color={`${theme.accent}55`} padding={{ top: 0, bottom: 0, left: 6, right: 6 }}>
              {item}
            </Highlight>
          ) : (
            <StrikeThrough progress={useDraw(delay + 8 + i * 6)} color={theme.ink} strokeWidth={3}>
              {item}
            </StrikeThrough>
          )}
        </div>
      ))}
    </div>
  );
  return (
    <Area>
      {title && (
        <Heading style={rise(useEnter(0))}>
          <Rich text={title} />
        </Heading>
      )}
      <div style={{ display: "flex", gap: 40 }}>
        {column(left, false, 4)}
        {column(right, true, 14)}
      </div>
    </Area>
  );
};

// A simple bar chart. { title?, unit?, bars: [{label, value}] } The largest bar is in the accent.
export const Bars = ({ title, unit = "", bars }: { title?: string; unit?: string; bars: { label: string; value: number }[] }) => {
  const theme = useTheme();
  const area = useArea();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const max = Math.max(...bars.map((b) => b.value), 1);
  const size = area.kind === "sheet" ? 40 : 46;
  return (
    <Area>
      {title && (
        <Heading style={rise(useEnter(0))}>
          <Rich text={title} />
        </Heading>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: area.kind === "sheet" ? 14 : 26 }}>
        {bars.map((bar, i) => {
          const grow = interpolate(frame - 6 - i * 5, [0, fps * 0.7], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (t) => 1 - (1 - t) ** 3 });
          const top = bar.value === max;
          return (
            <div key={i}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: size, fontWeight: 600, marginBottom: 6 }}>
                <span>{bar.label}</span>
                <span style={{ fontFamily: FONT_SERIF, fontSize: size * 1.3, color: top ? theme.accent : theme.ink }}>
                  {Math.round(bar.value * grow).toLocaleString("en-US")}
                  {unit}
                </span>
              </div>
              <div style={{ height: size * 0.8, borderRadius: 4, background: `${theme.ink}14` }}>
                <div style={{ height: "100%", width: `${(bar.value / max) * 100 * grow}%`, borderRadius: 4, background: top ? theme.accent : theme.ink }} />
              </div>
            </div>
          );
        })}
      </div>
    </Area>
  );
};

// A line worth repeating. { text, by? }
export const Quote = ({ text, by }: { text: string; by?: string }) => {
  const theme = useTheme();
  const area = useArea();
  return (
    <Area>
      <div style={{ ...rise(useEnter(4)), fontFamily: FONT_SERIF, fontSize: area.kind === "sheet" ? 104 : 120, lineHeight: 1.0, letterSpacing: -1 }}>
        <span style={{ color: theme.accent }}>“</span>
        <Rich text={text} />
        <span style={{ color: theme.accent }}>”</span>
      </div>
      {by && <div style={{ ...rise(useEnter(10)), fontSize: 30, fontWeight: 600, letterSpacing: 4, textTransform: "uppercase", opacity: 0.6 }}>{by}</div>}
    </Area>
  );
};

// A phone with a chat appearing message by message. Best full screen. { title?, name?, messages: [{from: "me" | "them", text}] }
export const Chat = ({ title, name = "Chat", messages }: { title?: string; name?: string; messages: { from: "me" | "them"; text: string }[] }) => {
  const theme = useTheme();
  const { fps } = useVideoConfig();
  const step = Math.round(fps * 0.6);
  return (
    <Area style={{ alignItems: "center", gap: 36 }}>
      {title && (
        <Heading size={84} style={{ ...rise(useEnter(0)), textAlign: "center" }}>
          <Rich text={title} />
        </Heading>
      )}
      <div style={{ ...pop(useEnter(2), 0.94), width: 720, borderRadius: 60, background: theme.ink, padding: 16, boxShadow: "0 30px 60px rgba(40,30,10,0.25)", transform: `${pop(useEnter(2), 0.94).transform} rotate(-1.5deg)` }}>
        <div style={{ borderRadius: 46, background: "#FBFAF7", overflow: "hidden", minHeight: 760, display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "30px 30px 20px", textAlign: "center", fontWeight: 600, fontSize: 32, color: theme.ink, borderBottom: `1px solid ${theme.ink}22` }}>{name}</div>
          <div style={{ padding: 26, display: "flex", flexDirection: "column", gap: 16 }}>
            {messages.map((m, i) => (
              <Bubble key={i} delay={8 + i * step} mine={m.from === "me"}>
                {m.text}
              </Bubble>
            ))}
          </div>
        </div>
      </div>
    </Area>
  );
};

const Bubble = ({ delay, mine, children }: { delay: number; mine: boolean; children: React.ReactNode }) => {
  const theme = useTheme();
  const enter = useEnter(delay, 10);
  return (
    <div
      style={{
        ...pop(enter, 0.8),
        alignSelf: mine ? "flex-end" : "flex-start",
        transformOrigin: mine ? "right bottom" : "left bottom",
        maxWidth: "80%",
        padding: "18px 24px",
        borderRadius: 28,
        fontSize: 32,
        lineHeight: 1.3,
        background: mine ? theme.accent : "#ECE7DC",
        color: mine ? "#fff" : theme.ink,
      }}
    >
      {children}
    </div>
  );
};

// A picture they sent (a file in the work folder), taped onto the paper, with a slow push-in. { src, caption? }
export const Picture = ({ src, caption }: { src: string; caption?: string }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const zoom = interpolate(frame, [0, fps * 6], [1, 1.06]);
  const enter = useEnter(0);
  return (
    <Area style={{ alignItems: "center", gap: 34 }}>
      <div style={{ ...pop(enter, 0.92), position: "relative", width: "100%", background: "#fff", padding: 18, boxShadow: "0 24px 50px rgba(40,30,10,0.22)", transform: `${pop(enter, 0.92).transform} rotate(1.2deg)` }}>
        <div style={{ overflow: "hidden" }}>
          <Img src={staticFile(src)} style={{ width: "100%", display: "block", transform: `scale(${zoom})` }} />
        </div>
        <div style={{ position: "absolute", top: -22, left: "50%", width: 180, height: 46, marginLeft: -90, background: "rgba(255,240,200,0.75)", transform: "rotate(-3deg)" }} />
      </div>
      {caption && (
        <Heading size={64} style={{ ...rise(useEnter(8)), textAlign: "center" }}>
          <Rich text={caption} />
        </Heading>
      )}
    </Area>
  );
};

// --- Over the video (the speaker stays in full view) ---

// Who they are: a small printed label. { name, role? }
export const NameTag = ({ name, role }: { name: string; role?: string }) => {
  const theme = useTheme();
  const enter = useEnter(0);
  return (
    <div style={{ position: "absolute", left: SAFE.side, top: 1000, ...rise(enter, 24) }}>
      <div style={{ ...paperStyle(theme.paper), color: theme.ink, padding: "18px 28px 20px", transform: "rotate(-2deg)", boxShadow: "0 12px 30px rgba(0,0,0,0.3)" }}>
        <div style={{ fontFamily: FONT_SERIF, fontSize: 68, lineHeight: 1 }}>{name}</div>
        {role && <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: 3, textTransform: "uppercase", marginTop: 8, color: theme.accent }}>{role}</div>}
      </div>
    </div>
  );
};

// A quick aside: a note stuck up in the open space above their head. { text, emoji? }
export const Callout = ({ text, emoji }: { text: string; emoji?: string }) => {
  const theme = useTheme();
  const enter = useEnter(0, 12);
  return (
    <div style={{ position: "absolute", top: SAFE.top + 30, left: SAFE.side, right: SAFE.side, display: "flex", justifyContent: "center" }}>
      <div
        style={{
          ...paperStyle(theme.paper),
          opacity: enter,
          transform: `scale(${interpolate(enter, [0, 1], [0.85, 1])}) rotate(${interpolate(enter, [0, 1], [-6, 1.5])}deg)`,
          color: theme.ink,
          display: "flex",
          gap: 18,
          alignItems: "center",
          padding: "24px 34px",
          maxWidth: 860,
          boxShadow: "0 16px 40px rgba(0,0,0,0.3)",
        }}
      >
        {emoji && <span style={{ fontSize: 56 }}>{emoji}</span>}
        <span style={{ fontSize: 58, fontWeight: 600, lineHeight: 1.12 }}>
          <Rich text={text} />
        </span>
      </div>
    </div>
  );
};

// A few big words in the open space above their head, underlined by hand. { text } (two to five words)
export const Punch = ({ text }: { text: string }) => {
  const theme = useTheme();
  const enter = useEnter(0, 10);
  return (
    <div style={{ position: "absolute", top: SAFE.top + 60, left: SAFE.side, right: SAFE.side }}>
      <div style={{ ...rise(enter, 30), fontFamily: FONT_SERIF, fontSize: 132, lineHeight: 0.98, letterSpacing: -2, color: "#fff", textShadow: softShadow }}>
        <Underline progress={useDraw(8)} color={theme.accent} strokeWidth={6}>
          <Rich text={text} />
        </Underline>
      </div>
    </div>
  );
};
