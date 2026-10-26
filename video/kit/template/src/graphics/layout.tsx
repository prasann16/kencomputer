import { createContext, type CSSProperties, type ReactNode, useContext } from "react";

import { useTheme } from "../theme";

// Where a graphic's content goes: the paper sheet under the speaker, or the whole screen. GraphicFrame sets it.
export interface AreaBox {
  top: number;
  bottom: number; // px from the bottom of the frame
  side: number;
  kind: "sheet" | "full" | "over";
}
export const AreaContext = createContext<AreaBox>({ top: 0, bottom: 0, side: 0, kind: "over" });
export const useArea = () => useContext(AreaContext);

// Lays children out inside the current area, on paper colors.
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
        color: theme.ink,
        fontFamily: theme.font,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

// Faint paper fibres, so graphics feel printed rather than flat.
const fibres =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='300' height='300'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.1 0 0 0 0 0.08 0 0 0 0 0.05 0 0 0 0.09 0'/></filter><rect width='300' height='300' filter='url(%23n)'/></svg>\")";

export const paperStyle = (color: string): CSSProperties => ({ backgroundColor: color, backgroundImage: fibres });

// Text where *starred words* are set in the accent serif italic: "Europe *figured it out*".
export const Rich = ({ text, accent }: { text: string; accent?: string }) => {
  const theme = useTheme();
  const parts = text.split(/(\*[^*]+\*)/g).filter(Boolean);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("*") && part.endsWith("*") ? (
          <span key={i} style={{ fontFamily: FONT_SERIF, fontStyle: "italic", fontWeight: 400, color: accent ?? theme.accent }}>
            {part.slice(1, -1)}
          </span>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
};

export const FONT_SERIF = "'Instrument Serif', serif";

// A serif headline in ink.
export const Heading = ({ children, size, style }: { children: ReactNode; size?: number; style?: CSSProperties }) => {
  const theme = useTheme();
  const area = useArea();
  return (
    <div style={{ fontFamily: theme.headingFont, fontWeight: 400, fontSize: size ?? (area.kind === "sheet" ? 96 : 120), lineHeight: 1.02, letterSpacing: -1, ...style }}>
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
