import {Composition} from 'remotion';
import {Cards} from './Reel';
import reel from '../reel.json';

// The cards, sized and timed by reel.json. Ken renders one card at a time with --props={"only": n}.
export const Root = () => (
  <Composition id="Cards" component={Cards} defaultProps={{only: undefined}} fps={reel.fps ?? 30}
    width={reel.width ?? 1080} height={reel.height ?? 1920}
    durationInFrames={Math.max(1, Math.ceil(reel.duration * (reel.fps ?? 30)))} />
);
