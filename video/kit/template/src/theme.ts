// The reel's design, from props.style. There is no house look: every choice (fonts, colours, texture, marks) is made
// for this reel (design.md), and anything not chosen is plain and neutral (Inter, white, no grain, no paper).
import { createContext, useContext } from "react";

import { fontStack, loadFont } from "./typefaces";

export interface Style {
  // Fonts: any Google Fonts family by name ("Space Grotesk", "Fraunces", "DM Mono"…), loaded when the reel renders.
  font?: string; // captions and body text
  headingFont?: string; // headlines on graphics (default: font)
  accentFont?: string; // the hook, emphasized and *starred* words, big numbers (default: font)
  captionWeight?: number; // 100 to 900 (default 600)
  headingWeight?: number; // default 700
  accentWeight?: number; // default 800
  accentItalic?: boolean; // emphasis in italic (only if the accent font has one)
  fonts?: string[]; // more families your own graphics use, loaded with the rest
  hookSize?: number; // the hook's type size (default 112)
  accent?: string; // emphasis, numbers, highlights (default: textColor, so emphasis is weight and size only)
  textColor?: string; // captions and text over the video (default white)
  paper?: string; // background of panel graphics (sheet, full)
  ink?: string; // text on panels
  captions?: boolean;
  captionSize?: number;
  captionY?: number; // captions' bottom edge, px from the bottom of the frame (default 610)
  uppercase?: boolean;
  wordsPerPageMs?: number;
  zoomCuts?: boolean; // a slight punch-in on some jump cuts (default true)
  grain?: boolean; // film grain and a vignette (default off)
  paperTexture?: boolean; // faint fibres on panels (default off)
  marks?: "hand" | "clean" | "none"; // how the example graphics underline and circle things (default none)
}

export interface Theme {
  font: string;
  headingFont: string;
  accentFont: string;
  captionWeight: number;
  headingWeight: number;
  accentWeight: number;
  accentItalic: boolean;
  paperTexture: boolean;
  marks: "hand" | "clean" | "none";
  accent: string;
  text: string;
  paper: string;
  ink: string;
}

// Names saved in older styles (memory/video-style.md): the bundled faces they meant.
const OLD_NAMES: Record<string, string> = { Serif: "Instrument Serif" };
const family = (name: string | undefined, fallback: string) => (name ? OLD_NAMES[name] ?? name : fallback);

export function themeFrom(style: Style = {}): Theme {
  const font = family(style.font, "Inter");
  const headingFont = family(style.headingFont, font);
  const accentFont = family(style.accentFont, font);
  for (const f of new Set([font, headingFont, accentFont, ...(style.fonts ?? [])])) loadFont(f);
  const text = style.textColor ?? "#FFFFFF";
  return {
    font: fontStack(font),
    headingFont: fontStack(headingFont),
    accentFont: fontStack(accentFont),
    captionWeight: style.captionWeight ?? 600,
    headingWeight: style.headingWeight ?? 700,
    accentWeight: style.accentWeight ?? 800,
    accentItalic: style.accentItalic ?? false,
    paperTexture: style.paperTexture === true,
    marks: style.marks ?? "none",
    accent: style.accent ?? text,
    text,
    paper: style.paper ?? "#FFFFFF",
    ink: style.ink ?? "#111111",
  };
}

export const ThemeContext = createContext<Theme>(themeFrom());
export const useTheme = () => useContext(ThemeContext);

// Instagram and TikTok cover the top ~250 px and the bottom ~420 to 480 px with their own UI (and the right ~140 px).
export const SAFE = { top: 250, bottom: 420, side: 80 } as const;

// A "sheet" panel slides up from the bottom to SHEET_TOP while the speaker lifts by SHEET_LIFT, so their face stays
// in view above it. One placement among several, never the default.
export const SHEET_TOP = 940;
export const SHEET_LIFT = 340;

// Soft shadow that keeps light text readable on any footage, without a hard outline.
export const softShadow = "0 2px 4px rgba(0,0,0,0.35), 0 6px 28px rgba(0,0,0,0.45)";
