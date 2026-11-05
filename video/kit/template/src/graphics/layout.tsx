import { createContext, type CSSProperties, type ReactNode, useContext } from "react";

import { Mark } from "../kit/tools";
import { type Theme, useTheme } from "../theme";

// Where a graphic's content goes: over the video (inside the safe zones), a panel under the speaker ("sheet"), or a
// panel over the whole screen ("full"). GraphicFrame sets it.
export interface AreaBox {
  top: number;
  bottom: number; // px from the bottom of the frame
  side: number;
  kind: "sheet" | "full" | "over";
}
export const AreaContext = createContext<AreaBox>({ top: 0, bottom: 0, side: 0, kind: "over" });
export const useArea = () => useContext(AreaContext);

// Lays children out inside the current area: in the panel's ink on a panel, in the reel's text colour over the video.
export const Area = ({ children, style }: { children: ReactNode; style?: CSSProperties }) => {
  const theme = useTheme();
  const area = useArea();
  return (
    <div
      style={{
        position: "absolute",
        top: area.top,
        bottom: area.bottom,
        left: area.side,
        right: area.side,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: area.kind === "sheet" ? 22 : 40,
        color: area.kind === "over" ? theme.text : theme.ink,
        fontFamily: theme.font,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

// Faint paper fibres, for a reel whose design calls for printed paper (style.paperTexture).
const fibres =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='300' height='300'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.1 0 0 0 0 0.08 0 0 0 0 0.05 0 0 0 0.09 0'/></filter><rect width='300' height='300' filter='url(%23n)'/></svg>\")";

export const paperStyle = (color: string, texture = false): CSSProperties => ({ backgroundColor: color, backgroundImage: texture ? fibres : undefined });

// The accent type (style.accentFont and accentWeight): italic only where the design asked for it.
export const accentType = (theme: Theme, italic = false): CSSProperties => ({
  fontFamily: theme.accentFont,
  fontWeight: theme.accentWeight,
  fontStyle: italic && theme.accentItalic ? "italic" : "normal",
});

// Text where *starred words* are set in the accent: "Europe *figured it out*".
export const Rich = ({ text, accent }: { text: string; accent?: string }) => {
  const theme = useTheme();
  const parts = text.split(/(\*[^*]+\*)/g).filter(Boolean);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("*") && part.endsWith("*") ? (
          <span key={i} style={{ ...accentType(theme, true), color: accent ?? theme.accent }}>
            {part.slice(1, -1)}
          </span>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
};

// A headline in the reel's heading font.
export const Heading = ({ children, size, style }: { children: ReactNode; size?: number; style?: CSSProperties }) => {
  const theme = useTheme();
  const area = useArea();
  return (
    <div style={{ fontFamily: theme.headingFont, fontWeight: theme.headingWeight, fontSize: size ?? (area.kind === "sheet" ? 88 : 108), lineHeight: 1.04, letterSpacing: -1, ...style }}>
      {children}
    </div>
  );
};

// A small label above a headline.
export const Kicker = ({ children, style }: { children: ReactNode; style?: CSSProperties }) => {
  const theme = useTheme();
  return (
    <div style={{ fontFamily: theme.font, fontWeight: 600, fontSize: 28, letterSpacing: 5, textTransform: "uppercase", color: theme.accent, ...style }}>
      {children}
    </div>
  );
};

// A mark the way the reel's design draws marks (style.marks): by hand, clean, or none. `meaning` marks (a strike that
// says "this is the old way") still show when the design has no marks, drawn clean.
export const ThemedMark = ({ kind, progress, color, meaning = false, strokeWidth, children }: { kind: "marker" | "underline" | "box" | "circle" | "strike"; progress: number; color?: string; meaning?: boolean; strokeWidth?: number; children: ReactNode }) => {
  const theme = useTheme();
  const style = theme.marks === "none" ? (meaning ? "clean" : "none") : theme.marks;
  if (style === "none") return <>{children}</>;
  return (
    <Mark kind={kind} hand={style === "hand"} progress={progress} color={color ?? theme.accent} strokeWidth={strokeWidth}>
      {children}
    </Mark>
  );
};
