// The live preview: the reel plays in the browser (Remotion's Player) straight from props.json and the footage, with
// nothing rendered. The same code draws it as the render does, so it looks like the finished reel. props.json is read
// each time the page opens, so after an edit to it a reload shows the change; only code changes need a new build.
// ?t=<seconds> opens it paused at that moment.
import { Player, type PlayerRef } from "@remotion/player";
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { Reel, type ReelProps } from "./src/Reel";

const FPS = 30;

// Files the reel points at (its footage, pictures) load from beside the page: the reel's own folder.
(window as unknown as { remotion_staticBase: string }).remotion_staticBase = location.pathname.replace(/\/[^/]*$/, "");
// A family the reel loads from Google Fonts comes from the copy preview.mjs saved instead: the page may only load
// files from where it's served.
const realFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const m = url.match(/^https:\/\/fonts\.googleapis\.com\/css2\?family=([^:&]+)/);
  if (!m) return realFetch(input, init);
  return realFetch(`.preview/fonts/${encodeURIComponent(decodeURIComponent(m[1].replace(/\+/g, " ")))}.css`);
};

const App = () => {
  const [props, setProps] = useState<ReelProps | null>(null);
  const [error, setError] = useState("");
  const player = useRef<PlayerRef>(null);
  useEffect(() => {
    fetch(`props.json?${Date.now()}`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`props.json: ${r.status}`))))
      .then(setProps, (e) => setError(String(e.message ?? e)));
  }, []);
  useEffect(() => {
    const t = Number(new URLSearchParams(location.search).get("t"));
    if (props && Number.isFinite(t) && t > 0) player.current?.seekTo(Math.round(t * FPS));
  }, [props]);
  if (error) return <p className="msg">This preview couldn't load ({error}). Ask Ken for a new link.</p>;
  if (!props) return <p className="msg">Loading…</p>;
  return (
    <Player
      ref={player}
      component={Reel}
      inputProps={props}
      durationInFrames={Math.max(1, Math.round(props.durationSeconds * FPS))}
      compositionWidth={1080}
      compositionHeight={1920}
      fps={FPS}
      controls
      clickToPlay
      doubleClickToFullscreen
      allowFullscreen
      style={{ width: "100%", height: "100%" }}
      acknowledgeRemotionLicense
    />
  );
};

createRoot(document.getElementById("app")!).render(<App />);
