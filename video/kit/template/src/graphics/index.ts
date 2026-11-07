// Every graphic Ken can place, by name: "design" (toolbox items in props.json) or a component Ken writes for the
// reel in this folder and registers here. There are no ready-made graphics: each reel's are made for it.
// All float over the video unless props.json sets "placement": "sheet" (a panel under the speaker, who lifts to
// stay in view) or "full" (a panel over everything, the voice keeps playing).
import type { ComponentType } from "react";

import { Design } from "./design";

export type Placement = "sheet" | "full" | "over";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const GRAPHICS: Record<string, { component: ComponentType<any>; placement: Placement }> = {
  design: { component: Design, placement: "over" },
};
