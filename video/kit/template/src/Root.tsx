import { Composition } from "remotion";

import { CaptionsLayer, GraphicsLayer, Reel, type ReelProps } from "./Reel";

const FPS = 30;

const defaults: ReelProps = { video: "cut.mp4", durationSeconds: 5, words: [] };

const frames = ({ props }: { props: ReelProps }) => ({ durationInFrames: Math.max(1, Math.round(props.durationSeconds * FPS)) });

export const Root = () => (
  <>
  <Composition
    id="Reel"
    component={Reel}
    width={1080}
    height={1920}
    fps={FPS}
    durationInFrames={defaults.durationSeconds * FPS}
    defaultProps={defaults}
    calculateMetadata={({ props }) => ({ durationInFrames: Math.max(1, Math.round(props.durationSeconds * FPS)) })}
  />

  <Composition id="GraphicsLayer" component={GraphicsLayer} width={1080} height={1920} fps={FPS} durationInFrames={1} defaultProps={defaults} calculateMetadata={frames} />
  <Composition
    id="CaptionsLayer"
    component={CaptionsLayer}
    width={1080}
    height={1920}
    fps={FPS}
    durationInFrames={1}
    defaultProps={{ ...defaults, pick: [0] }}
    // As long as the reel, so captions late in it still draw; compose.mjs only asks for the first pick.length frames.
    calculateMetadata={({ props }) => ({ durationInFrames: Math.max(props.pick.length, Math.round(props.durationSeconds * FPS)) })}
  />
  </>
);
