// The infographic cards of a reel, on a transparent background. Ken renders each card on its own
// (`only`) and lays it on the footage with ffmpeg, which also draws the captions. Everything here
// comes from reel.json; change that file, not this one, for a normal video. Add a new kind of card
// here only when a video needs one the cards below can't make.
import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {loadFont as montserrat} from '@remotion/google-fonts/Montserrat';
import {loadFont as inter} from '@remotion/google-fonts/Inter';
import {loadFont as poppins} from '@remotion/google-fonts/Poppins';
import {loadFont as bebas} from '@remotion/google-fonts/BebasNeue';
import {loadFont as serif} from '@remotion/google-fonts/InstrumentSerif';
import reel from '../reel.json';

type Item = {label: string; at: number};
type Scene = {
  from: number; to: number; top?: number;
  kicker?: string; title?: string; text?: string;
  chips?: Item[]; bars?: Item[]; checks?: Item[]; big?: string; image?: string;
};
type Reel = {
  duration: number; scenes?: Scene[];
  end?: {from: number; title: string; subtitle?: string};
  style?: {font?: string; accent?: string; text?: string; card?: string};
};

const R = reel as Reel;
const FONTS: Record<string, () => {fontFamily: string}> = {
  Montserrat: () => montserrat('normal', {weights: ['600', '800', '900']}),
  Inter: () => inter('normal', {weights: ['500', '700', '900']}),
  Poppins: () => poppins('normal', {weights: ['500', '700', '900']}),
  'Bebas Neue': () => bebas(),
  'Instrument Serif': () => serif(),
};
const {fontFamily} = (FONTS[R.style?.font ?? 'Montserrat'] ?? FONTS.Montserrat)();
const ACCENT = R.style?.accent ?? '#FFD60A';
const INK = R.style?.text ?? '#FFFFFF';
const CARD = R.style?.card ?? 'rgba(14,14,18,0.78)';

const useT = () => useCurrentFrame() / useVideoConfig().fps;
const usePop = (at: number) => {
  const frame = useCurrentFrame(), {fps} = useVideoConfig();
  return spring({frame: frame - Math.round(at * fps), fps, config: {damping: 14, stiffness: 180}});
};
// *word* in a title or text is drawn in the accent colour.
const accent = (s: string) => s.split(/\*([^*]+)\*/g).map((p, i) => (i % 2 ? <span key={i} style={{color: ACCENT}}>{p}</span> : p));

const Chip: React.FC<Item> = ({label, at}) => {
  const p = usePop(at);
  return <div style={{opacity: p, transform: `scale(${0.6 + 0.4 * p})`, background: 'rgba(255,255,255,0.14)', fontWeight: 800,
    fontSize: 40, padding: '14px 26px', borderRadius: 999, border: '2px solid rgba(255,255,255,0.2)'}}>{label}</div>;
};

const Bar: React.FC<Item> = ({label, at}) => {
  const t = useT();
  const w = interpolate(t, [at, at + 1.2], [0.08, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  return (
    <div style={{marginTop: 22}}>
      <div style={{fontWeight: 800, fontSize: 40, marginBottom: 10}}>{accent(label)}</div>
      <div style={{height: 26, borderRadius: 13, background: 'rgba(255,255,255,0.12)'}}>
        <div style={{height: '100%', width: `${w * 100}%`, borderRadius: 13, background: ACCENT}} />
      </div>
    </div>
  );
};

const Check: React.FC<Item> = ({label, at}) => {
  const p = usePop(at), on = p > 0.3;
  return (
    <div style={{display: 'flex', alignItems: 'center', gap: 22, marginTop: 18, opacity: 0.35 + 0.65 * p}}>
      <div style={{width: 56, height: 56, borderRadius: 16, border: `4px solid ${on ? ACCENT : 'rgba(255,255,255,0.5)'}`,
        background: on ? ACCENT : 'transparent', color: '#111', fontWeight: 900, fontSize: 40,
        display: 'flex', alignItems: 'center', justifyContent: 'center'}}>{on ? '✓' : ''}</div>
      <div style={{fontWeight: 800, fontSize: 46}}>{accent(label)}</div>
    </div>
  );
};

// A card that pops in at `from` and fades out at `to`.
const Card: React.FC<{s: Scene}> = ({s}) => {
  const t = useT(), enter = usePop(s.from);
  if (t < s.from || t >= s.to) return null;
  const exit = interpolate(t, [s.to - 0.2, s.to], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', top: s.top ?? 130, left: 60, right: 60, opacity: Math.min(enter, exit), fontFamily, color: INK,
      transform: `translateY(${(1 - enter) * -40}px) scale(${0.94 + 0.06 * enter})`}}>
      <div style={{background: CARD, border: '2px solid rgba(255,255,255,0.12)', borderRadius: 40, padding: '34px 40px',
        boxShadow: '0 20px 60px rgba(0,0,0,0.35)', textAlign: s.big ? 'center' : 'left'}}>
        {s.kicker && <div style={{display: 'inline-block', background: ACCENT, color: '#111', fontWeight: 900, fontSize: 30, letterSpacing: 2,
          padding: '8px 18px', borderRadius: 14, textTransform: 'uppercase', marginBottom: 18}}>{s.kicker}</div>}
        {s.title && <div style={{fontWeight: 900, fontSize: s.title.length > 40 ? 58 : 72, lineHeight: 1.05, letterSpacing: -1}}>{accent(s.title)}</div>}
        {s.text && <div style={{fontWeight: 600, fontSize: 40, lineHeight: 1.3, marginTop: 14, opacity: 0.9}}>{accent(s.text)}</div>}
        {s.big && <div style={{fontWeight: 900, fontSize: 96, color: ACCENT, letterSpacing: -2}}>{s.big}</div>}
        {s.image && <img src={staticFile(s.image)} style={{width: '100%', borderRadius: 24, marginTop: 18}} />}
        {s.chips && <div style={{display: 'flex', flexWrap: 'wrap', gap: 16, marginTop: 8}}>{s.chips.map((c) => <Chip key={c.label} {...c} />)}</div>}
        {s.bars?.map((b) => <Bar key={b.label} {...b} />)}
        {s.checks?.map((c) => <Check key={c.label} {...c} />)}
      </div>
    </div>
  );
};

const EndCard = () => {
  const t = useT(), p = usePop(R.end?.from ?? 0);
  if (!R.end || t < R.end.from) return null;
  return (
    <AbsoluteFill style={{background: `rgba(0,0,0,${0.55 * p})`, alignItems: 'center', justifyContent: 'center', fontFamily, color: INK}}>
      <div style={{textAlign: 'center', transform: `scale(${0.85 + 0.15 * p})`, opacity: p, padding: '0 60px'}}>
        <div style={{fontWeight: 900, fontSize: 96, color: ACCENT, letterSpacing: -2}}>{R.end.title}</div>
        {R.end.subtitle && <div style={{fontWeight: 800, fontSize: 44, marginTop: 12}}>{R.end.subtitle}</div>}
      </div>
    </AbsoluteFill>
  );
};

export const Cards: React.FC<{only?: number | 'end'}> = ({only}) => (
  <AbsoluteFill>
    {(R.scenes ?? []).map((s, i) => (only === undefined || only === i) && <Card key={i} s={s} />)}
    {(only === undefined || only === 'end') && <EndCard />}
  </AbsoluteFill>
);
