// Every graphic Ken can place, by name. Build a reel's graphics from the toolbox (../kit/tools.tsx) or with the
// "design" graphic in props.json; the others are EXAMPLES of the toolbox in use, not a set to drop in as they are
// (dropped in unchanged, every reel would look alike). They take every visual choice from the reel's design.
// All float over the video unless props.json sets "placement": "sheet" (a panel under the speaker, who lifts to
// stay in view) or "full" (a panel over everything, the voice keeps playing).
import type { ComponentType } from "react";

import { Bars, Callout, Chat, Compare, List, NameTag, Picture, Punch, Quote, Stat, Title } from "./basics";
import { Design } from "./design";
import { Browser, Eaten, Flow, Focus, Obsolete, Timeline } from "./explainers";

export type Placement = "sheet" | "full" | "over";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const GRAPHICS: Record<string, { component: ComponentType<any>; placement: Placement }> = {
  design: { component: Design, placement: "over" },
  // Examples:
  title: { component: Title, placement: "over" },
  stat: { component: Stat, placement: "over" },
  list: { component: List, placement: "over" },
  compare: { component: Compare, placement: "over" },
  bars: { component: Bars, placement: "over" },
  quote: { component: Quote, placement: "over" },
  chat: { component: Chat, placement: "over" },
  picture: { component: Picture, placement: "over" },
  nameTag: { component: NameTag, placement: "over" },
  callout: { component: Callout, placement: "over" },
  punch: { component: Punch, placement: "over" },
  eaten: { component: Eaten, placement: "over" },
  obsolete: { component: Obsolete, placement: "over" },
  focus: { component: Focus, placement: "over" },
  browser: { component: Browser, placement: "over" },
  flow: { component: Flow, placement: "over" },
  timeline: { component: Timeline, placement: "over" },
};
