import {Composition} from 'remotion';
import {Reel} from './Reel';
import reel from '../reel.json';

// One composition, sized and timed by reel.json.
export const Root = () => (
  <Composition id="Reel" component={Reel} fps={reel.fps ?? 30} width={reel.width ?? 1080} height={reel.height ?? 1920}
    durationInFrames={Math.max(1, Math.ceil(reel.duration * (reel.fps ?? 30)))} />
);
