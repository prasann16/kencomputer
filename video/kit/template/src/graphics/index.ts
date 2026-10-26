// Every graphic Ken can place, by name. To add one, write a component (see basics.tsx) and add it here.
// "sheet" graphics slide up on paper under the speaker, who lifts to stay in view; "full" ones wipe paper over
// the whole screen (the voice keeps playing); "over" ones float on the video.
import type { ComponentType } from "react";

import { Bars, Callout, Chat, Compare, List, NameTag, Picture, Punch, Quote, Stat, Title } from "./basics";
import { Browser, Eaten, Flow, Focus, Obsolete, Timeline } from "./explainers";

export type Placement = "sheet" | "full" | "over";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const GRAPHICS: Record<string, { component: ComponentType<any>; placement: Placement }> = {
  title: { component: Title, placement: "full" },
  stat: { component: Stat, placement: "sheet" },
  list: { component: List, placement: "sheet" },
  compare: { component: Compare, placement: "sheet" },
  bars: { component: Bars, placement: "sheet" },
  quote: { component: Quote, placement: "sheet" },
  chat: { component: Chat, placement: "full" },
  picture: { component: Picture, placement: "full" },
  nameTag: { component: NameTag, placement: "over" },
  callout: { component: Callout, placement: "over" },
  punch: { component: Punch, placement: "over" },
  eaten: { component: Eaten, placement: "sheet" },
  obsolete: { component: Obsolete, placement: "sheet" },
  focus: { component: Focus, placement: "sheet" },
  browser: { component: Browser, placement: "over" },
  flow: { component: Flow, placement: "sheet" },
  timeline: { component: Timeline, placement: "sheet" },
};
