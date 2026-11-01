// Colors and fonts for one reel, taken from props.style. Each reel's look is designed for it; with nothing set,
// the defaults are a serif hook and accents, cream paper with fibres, and film grain.
import { loadFont } from "@remotion/fonts";
import { createContext, useContext } from "react";
import { continueRender, delayRender } from "remotion";

import interUrl from "./fonts/Inter.ttf";
import serifItalicUrl from "./fonts/InstrumentSerif-Italic.ttf";
import serifUrl from "./fonts/InstrumentSerif-Regular.ttf";
import montserratUrl from "./fonts/Montserrat-Black.ttf";

// Bundled with the project so every render looks the same, in the box or on a Mac.
const fontsReady = delayRender("Loading fonts");
Promise.all([
  loadFont({ family: "Inter", url: interUrl, weight: "100 900" }),
  loadFont({ family: "Instrument Serif", url: serifUrl, style: "normal" }),
  loadFont({ family: "Instrument Serif", url: serifItalicUrl, style: "italic" }),
  loadFont({ family: "Montserrat", url: montserratUrl, weight: "900" }),
]).then(() => continueRender(fontsReady));

export const FONTS = {
  Inter: "Inter, sans-serif",
  Serif: "'Instrument Serif', serif",
  Montserrat: "Montserrat, sans-serif", // the Black weight only, for bold uppercase captions
  Anton: "Anton, sans-serif", // installed in the box image
} as const;

export interface Style {
  font?: keyof typeof FONTS; // captions and body text
  headingFont?: keyof typeof FONTS; // headlines on graphics
  accentFont?: keyof typeof FONTS; // the hook, *starred* and emphasized words, big numbers and quotes (default Serif)
  hookSize?: number; // the hook's type size (default 128)
  accent?: string; // emphasized words, numbers, hand-drawn marks
  textColor?: string; // captions
  paper?: string; // background of graphics
  ink?: string; // text on graphics
  captions?: boolean;
  captionSize?: number;
  captionWeight?: number; // 100 to 900 in Inter (default 650)
  uppercase?: boolean;
  wordsPerPageMs?: number;
  zoomCuts?: boolean; // a hard punch-in on every other jump cut
  grain?: boolean; // film grain and a soft vignette over everything
  paperTexture?: boolean; // faint fibres on paper (default true); false for flat colour
}

export interface Theme {
  font: string;
  headingFont: string;
  accentFont: string;
  accentItalic: boolean; // only the serif has an italic
  accentWeight: number;
  paperTexture: boolean;
  accent: string;
  text: string;
  paper: string;
  ink: string;
}

export function themeFrom(style: Style = {}): Theme {
  const accentFont = FONTS[style.accentFont ?? "Serif"] ? (style.accentFont ?? "Serif") : "Serif";
  return {
    font: FONTS[style.font ?? "Inter"] ?? FONTS.Inter,
    headingFont: FONTS[style.headingFont ?? "Serif"] ?? FONTS.Serif,
    accentFont: FONTS[accentFont],
    accentItalic: accentFont === "Serif",
    accentWeight: accentFont === "Serif" ? 400 : accentFont === "Inter" ? 800 : 900,
    paperTexture: style.paperTexture !== false,
    accent: style.accent ?? "#FF7A3D",
    text: style.textColor ?? "#FFFFFF",
    paper: style.paper ?? "#F3EEE4",
    ink: style.ink ?? "#1A1814",
  };
}

export const ThemeContext = createContext<Theme>(themeFrom());
export const useTheme = () => useContext(ThemeContext);

// Instagram and TikTok cover the top ~250 px and the bottom ~420 to 480 px with their own UI.
export const SAFE = { top: 250, bottom: 420, side: 80 } as const;

// A "sheet" graphic slides paper up from the bottom to SHEET_TOP while the speaker lifts by SHEET_LIFT,
// so their face stays in view above it.
export const SHEET_TOP = 940;
export const SHEET_LIFT = 340;

// Soft shadow that keeps white text readable on any footage, without a hard outline.
export const softShadow = "0 2px 4px rgba(0,0,0,0.35), 0 6px 28px rgba(0,0,0,0.45)";
