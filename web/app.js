// Ken: one conversation, with tools and settings available when needed.
import { html, render, useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from '/vendor/preact-htm.js';

// ------------------------------------------------------------------ helpers

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.json !== undefined || opts.body !== undefined ? 'POST' : 'GET'), headers: {} };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers['Content-Type'] = 'application/json';
  } else if (opts.body !== undefined) {
    init.body = opts.body;
  }
  if (opts.timeout) init.signal = AbortSignal.timeout(opts.timeout);
  const r = await fetch(path, init);
  if (r.status === 401) { location.reload(); throw new Error('signed out'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// Files the user attached; they show in their message, not as Ken's file cards.
const ATTACHED = new Set();
async function upload(pid, list, attach) {
  const fd = new FormData();
  for (const f of list) fd.append('file', f, f.name);
  const saved = (await api(`/api/chats/${pid}/files${attach ? '?attach=1' : ''}`, { body: fd })).saved || [];
  if (attach) saved.forEach((name) => ATTACHED.add(name));
  return saved;
}

const ago = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const firstLine = (s) => String(s || '').trim().split('\n')[0];
const isImage = (name) => /\.(png|jpe?g|gif|webp|heic)$/i.test(name);
const parseHash = () => ({ pid: (location.hash.match(/^#\/p\/([a-z0-9-]+)/) || [])[1] || null });

// Just enough markdown for chat: code blocks, tables, `code`, **bold**, links. Never raw HTML.
const URL_RE = /(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"`*])/g;
function inline(text) {
  return text.split(/(`[^`\n]+`)/g).map((seg, i) => {
    if (i % 2) return html`<code>${seg.slice(1, -1)}</code>`;
    return seg.split(URL_RE).map((part, j) => {
      if (j % 2) return html`<a href=${part} target="_blank" rel="noopener">${part}</a>`;
      return part.split(/\*\*([^*\n]+)\*\*/g).map((p, k) => (k % 2 ? html`<b>${p}</b>` : p));
    });
  });
}
const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
function prose(text) {
  return text.split(/((?:^\|.*\|[ \t]*(?:\n|$)){2,})/m).map((part, i) => {
    const lines = part.trim().split('\n');
    if (i % 2 === 0 || !/^\|[\s:|-]+\|$/.test(lines[1] || '')) return inline(part);
    return html`<div class="table"><table><thead><tr>${cells(lines[0]).map((c) => html`<th>${inline(c)}</th>`)}</tr></thead>
      <tbody>${lines.slice(2).map((l) => html`<tr>${cells(l).map((c) => html`<td>${inline(c)}</td>`)}</tr>`)}</tbody></table></div>`;
  });
}
function md(text) {
  return String(text || '').split(/```[^\n]*\n?([\s\S]*?)```/g).map((block, i) =>
    i % 2 ? html`<pre><code>${block.replace(/\n$/, '')}</code></pre>` : prose(block));
}

// -------------------------------------------------------------------- icons

const Icon = {
  sun: html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`,
  plus: html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`,
  clip: html`<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5l-8.6 8.6a5 5 0 01-7.1-7.1l8.6-8.6a3.3 3.3 0 014.7 4.7l-8.6 8.6a1.7 1.7 0 01-2.4-2.4l7.9-7.9"/></svg>`,
  mic: html`<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v3"/></svg>`,
  send: html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>`,
  stop: html`<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`,
  play: html`<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5a1 1 0 0 1 1.5-.86l10 6.5a1 1 0 0 1 0 1.72l-10 6.5A1 1 0 0 1 8 18.5Z"/></svg>`,
  pause: html`<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>`,
  menu: html`<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></svg>`,
  file: html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5"/></svg>`,
  close: html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>`,
};

// ------------------------------------------------------------- small parts

function Chip({ project }) {
  if (!project) return html`<span class="chip" style="background:var(--track);color:var(--ink)">Ken</span>`;
  return html`<span class="chip" style=${{ background: `color-mix(in srgb, ${project.color} 14%, var(--card))`, color: `color-mix(in srgb, ${project.color} 75%, var(--ink))` }}>${project.name}</span>`;
}

const durationLabel = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
const COMMANDS = [['/new', 'Fresh conversation · memory stays'], ['/stop', 'Stop Ken’s current task'], ['/model', 'See or switch the AI model'], ['/coffee', 'Keep this Mac awake'], ['/decaf', 'Let this Mac sleep normally']];

function waveformPeaks(values, count = 48) {
  // Keep each interval's peak; point-sampling can miss whole words in a note.
  return Array.from({length:count}, (_, i) => {
    const start = Math.floor(i * values.length / count), end = Math.max(start + 1, Math.floor((i + 1) * values.length / count));
    let peak = 0;
    for (let n = start; n < end; n++) peak = Math.max(peak, values[n] || 0);
    return peak;
  });
}

function Waveform({ stream, peaks = [], onPeak, progress = 0 }) {
  const svg = useRef(null);
  useEffect(() => {
    if (!stream) return;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    const context = new Audio(), source = context.createMediaStreamSource(stream), analyser = context.createAnalyser();
    analyser.fftSize = 1024; source.connect(analyser); context.resume().catch(() => {});
    const data = new Float32Array(analyser.fftSize), history = Array(48).fill(0);
    let frame, last = 0;
    const draw = (now) => {
      if (now - last > 50) {
        last = now; analyser.getFloatTimeDomainData(data);
        const rms = Math.sqrt(data.reduce((sum, v) => sum + v * v, 0) / data.length);
        const level = Math.min(1, rms * 5); history.shift(); history.push(level); onPeak?.(level);
        [...(svg.current?.children || [])].forEach((bar, i) => { const height = Math.max(2, Math.sqrt(history[i]) * 26); bar.setAttribute('height', height); bar.setAttribute('y', (32-height)/2); });
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(frame); source.disconnect(); context.close().catch(() => {}); };
  }, [stream]);
  const bars = waveformPeaks(peaks), peak = Math.max(.001, ...bars);
  return html`<svg ref=${svg} class=${'voice-wave' + (stream ? ' live-wave' : '')} viewBox="0 0 192 32" preserveAspectRatio="none" role="img" aria-label=${stream ? 'Live microphone waveform' : 'Voice recording'}>${bars.map((p,i) => {
    const height = Math.max(2, Math.pow(p / peak, .65) * 26);
    return html`<rect x=${i*4} y=${(32-height)/2} width="2" height=${height} rx="1" class=${i / bars.length < progress ? 'played' : ''}/>`;
  })}</svg>`;
}

function VoiceNote({ src, peaks = [], seconds, pending, failed, onRetry, failure, transcript }) {
  const audio = useRef(null), [playing,setPlaying] = useState(false), [position,setPosition] = useState(0), [length,setLength] = useState(seconds || 0);
  // Upload completion and message acknowledgement must not restart playback.
  const source = useRef(src);
  const [error,setError] = useState('');
  useEffect(()=>{const player=audio.current;return()=>{player?.pause();if(source.current.startsWith('blob:'))URL.revokeObjectURL(source.current);};},[]);
  const play = async () => { try { if (playing) audio.current.pause(); else { if(audio.current.ended)audio.current.currentTime=0; await audio.current.play(); } setError(''); } catch { setError('Couldn’t play this recording.'); } };
  const progress = length ? Math.min(1, position / length) : 0;
  const seek = (e) => { const next = Number(e.target.value); try { audio.current.currentTime=next;setPosition(next); } catch {} };
  return html`<div class="voice-note"><div class="voice-note-row"><button class="voice-play" aria-label=${playing ? 'Pause voice note' : 'Play voice note'} onClick=${play}>${playing ? Icon.pause : Icon.play}</button>
    <div class="voice-seek">${peaks.length ? html`<${Waveform} peaks=${peaks} progress=${progress}/>` : html`<div class="voice-track"><span style=${{width:progress*100+'%'}}></span></div>`}<input type="range" min="0" max=${length || 0} step="0.1" value=${position} disabled=${!length} aria-label="Seek voice note" aria-valuetext=${durationLabel(position)+' of '+durationLabel(length)} onInput=${seek}/></div>
    <span class="voice-time">${durationLabel(playing || position > 0 ? position : length)}</span></div>
    <audio ref=${audio} src=${source.current} preload="metadata" onPlay=${()=>{document.querySelectorAll('.voice-note audio').forEach(other=>{if(other!==audio.current && !other.paused)other.pause();});setPlaying(true);}} onPause=${()=>setPlaying(false)} onEnded=${()=>{setPlaying(false);setPosition(0);}} onTimeUpdate=${()=>setPosition(audio.current.currentTime)} onLoadedMetadata=${()=>{if(Number.isFinite(audio.current.duration))setLength(audio.current.duration);}} onError=${()=>setError('Couldn’t play this recording.')}></audio>
    ${transcript ? html`<div class="voice-transcript">${transcript}</div>` : pending && html`<div class="voice-state" role="status"><span class="voice-pending" aria-hidden="true"></span>Listening…</div>`}
    ${error && html`<small>${error}</small>`}${failed && html`<div class="voice-error">${failure || 'Couldn’t process this voice note.'} ${/No microphone audio/i.test(failure||'') ? window.kenDesktop?.microphoneSettings && html`<button class="text-action" onClick=${()=>window.kenDesktop.microphoneSettings('input')}>Microphone settings</button>` : html`<button class="text-action" onClick=${onRetry}>Retry voice note</button>`}</div>`}</div>`;
}

function useRecorder(onAudio, toast) {
  const [status,setStatus] = useState('idle'), [stream,setStream] = useState(null), [seconds,setSeconds] = useState(0), [heard,setHeard]=useState(false), [error,setError]=useState('');
  const rec = useRef(null), alive = useRef(true), intent = useRef(false), starting = useRef(false), requestId = useRef(0), peaks = useRef([]), started = useRef(0);
  useEffect(() => () => { alive.current=false; if(rec.current){rec.current.onstop=null;if(rec.current.state!=='inactive')rec.current.stop();rec.current.stream.getTracks().forEach(t=>t.stop());} },[]);
  useEffect(() => { if(status!=='recording')return; const timer=setInterval(()=>setSeconds((Date.now()-started.current)/1000),250);return()=>clearInterval(timer); },[status]);
  const start = async () => {
    if(starting.current || status!=='idle')return;
    starting.current=true;const request=++requestId.current;setStatus('requesting');setError('');setHeard(false);let input;
    // macOS can sit on the permission step (a prompt behind other windows); say what to do.
    const waiting=setTimeout(()=>{if(alive.current && request===requestId.current)setError('Waiting for macOS to allow the microphone. Look for a permission prompt, or turn Ken on in Microphone settings.');},5000);
    try {
      if(window.kenDesktop?.microphonePermission && !await window.kenDesktop.microphonePermission())throw new Error('Microphone access is off. Allow Ken in macOS microphone settings.');
      if(!alive.current || request!==requestId.current)return;
      // Use the browser's normal speech capture, including microphone leveling.
      input=await navigator.mediaDevices.getUserMedia({audio:true});
      if(!input.getAudioTracks().some(track=>track.readyState==='live'))throw new Error('No working microphone is available. Check your sound input.');
      if(!alive.current || request!==requestId.current){input.getTracks().forEach(t=>t.stop());return;}
      const recorder=new MediaRecorder(input), chunks=[];
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      recorder.onstop=()=>{
        input.getTracks().forEach(t=>t.stop());if(!alive.current)return;
        setStream(null);setStatus('idle');
        if(intent.current && chunks.length)onAudio(new Blob(chunks,{type:recorder.mimeType}),peaks.current,(Date.now()-started.current)/1000);
      };
      recorder.onerror=()=>{intent.current=false;input.getTracks().forEach(t=>t.stop());setStream(null);setStatus('idle');toast('Recording stopped. Please try again.');};
      rec.current=recorder;intent.current=false;peaks.current=[];started.current=Date.now();setSeconds(0);setStream(input);recorder.start(250);setStatus('recording');setError('');
    }catch(e){input?.getTracks().forEach(t=>t.stop());if(alive.current && request===requestId.current){setStatus('idle');setError(e.name==='NotAllowedError'?'Microphone access is off. Allow Ken in macOS microphone settings.':e.message||'Could not start your microphone.');}}
    finally{clearTimeout(waiting);if(request===requestId.current)starting.current=false;}
  };
  const finish = (send) => {if(status==='requesting'){requestId.current++;starting.current=false;setStatus('idle');setError('');return;}if(rec.current?.state==='recording'){intent.current=send;setStatus('finishing');rec.current.stop();}};
  return {status,stream,seconds,start,finish,heard,error,peak:(p)=>{peaks.current.push(p);if(p>0.0000001)setHeard(true);}};
}

function Composer({ placeholder, onSend, onVoice, onAttach, voice, busy, onStop, toast, draft, dropped, onDraftUsed, storageKey, recordRequest }) {
  const savedDraft = (() => { try { return JSON.parse(sessionStorage.getItem('ken-draft:' + storageKey) || '{}'); } catch { return {}; } })();
  const [text, setText] = useState(savedDraft.text || '');
  const [files, setFiles] = useState(savedDraft.files || []);
  (savedDraft.files || []).forEach((name) => ATTACHED.add(name));
  useEffect(() => { sessionStorage.setItem('ken-draft:' + storageKey, JSON.stringify({ text, files })); }, [text, files, storageKey]);
  useEffect(() => { if (draft) { setText(draft.text); onDraftUsed(); requestAnimationFrame(() => ta.current?.focus()); } }, [draft]);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [uploading, setUploading] = useState(0);
  const ta = useRef(null);
  const picker = useRef(null);
  const id = useRef('c' + Math.random().toString(36).slice(2, 8)).current;
  const recorder = useRecorder((blob,peaks,seconds)=>onVoice(blob,peaks,seconds),toast);
  const recording = recorder.status === 'recording', voiceBusy = recorder.status !== 'idle';
  const commands = text.startsWith('/') && !text.includes(' ') ? COMMANDS.filter(([name])=>name.startsWith(text)) : [];
  const [commandIndex,setCommandIndex]=useState(0);
  useEffect(()=>setCommandIndex(0),[text]);
  useEffect(() => { if (recordRequest) recorder.start(); }, [recordRequest]);

  useEffect(() => {
    const el = ta.current;
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 200) + 'px'; }
  }, [text]);

  const attach = async (list) => {
    if (!onAttach || !list || !list.length) return;
    setUploading((n) => n + 1);
    try {
      const saved = await onAttach([...list]);
      setFiles((f) => [...new Set([...f, ...saved])]);
    } catch (e) { toast(e.message); }
    finally { setUploading((n) => n - 1); }
  };
  useEffect(() => { if (dropped) attach(dropped); }, [dropped]);
  const send = async () => {
    if (recording) { if(recorder.heard)recorder.finish(true); return; }
    const t = text.trim();
    if ((!t && !files.length) || sendingRef.current || uploading || voiceBusy) return;
    const sentFiles = files;
    sendingRef.current = true; setSending(true); setText(''); setFiles([]);
    try { await onSend(t, sentFiles); }
    catch (e) { toast(e.message); }
    finally { sendingRef.current = false; setSending(false); ta.current?.focus(); }
  };
  const onKey = (e) => {
    if(commands.length && ['ArrowDown','ArrowUp','Tab'].includes(e.key)){e.preventDefault();if(e.key==='Tab')setText(commands[commandIndex][0]+' ');else setCommandIndex((commandIndex+(e.key==='ArrowDown'?1:commands.length-1))%commands.length);return;}
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if(commands.length && !COMMANDS.some(([c])=>c===text)){setText(commands[commandIndex][0]+' ');}else send(); } };
  const ready = text.trim() || files.length;

  return html`<div class="composer-wrap">
    ${recorder.error && html`<div class="mic-error" role="alert">${recorder.error}${window.kenDesktop?.microphoneSettings && html` <button class="text-action" onClick=${()=>window.kenDesktop.microphoneSettings('privacy')}>Microphone settings</button>`}</div>`}
    ${recording && !recorder.heard && recorder.seconds>2 && html`<div class="mic-error" role="status">No sound from your microphone.${window.kenDesktop?.microphoneSettings && html` <button class="text-action" onClick=${()=>window.kenDesktop.microphoneSettings('input')}>Check input</button>`}</div>`}
    ${commands.length>0 && html`<div class="command-list" role="listbox" aria-label="Chat commands">${commands.map(([name,description],i)=>html`<button role="option" aria-selected=${i===commandIndex} onClick=${()=>{setText(name+' ');ta.current.focus();}}><b>${name}</b><span>${description}</span></button>`)}</div>`}
    ${uploading > 0 && html`<div class="upload-status" role="status">Attaching files…</div>`}
    ${files.length > 0 && html`<div class="attached">${files.map((f) => html`<span class="filechip">${isImage(f)?html`<img src=${`/api/chats/${storageKey}/files/${encodeURIComponent(f)}`} alt=${f}/>`:Icon.file}${f}
      <button class="x" aria-label=${'Remove ' + f} onClick=${() => setFiles(files.filter((x) => x !== f))}>${Icon.close}</button></span>`)}</div>`}
    <div class="composer" onClick=${(e) => { if (e.target === e.currentTarget) ta.current?.focus(); }}>
      ${voiceBusy ? html`<div class="recording-input"><button class="icon-btn" aria-label="Cancel recording" onClick=${()=>recorder.finish(false)}>${Icon.close}</button>${recording && html`<span class="recording-label"><i aria-hidden="true"></i>Recording</span>`}<${Waveform} stream=${recorder.stream} onPeak=${recorder.peak}/><span class="voice-time">${recorder.status==='requesting'?'Allow microphone…':durationLabel(recorder.seconds)}</span></div>` : html`<label class="sr" for=${id}>${placeholder}</label>
      <textarea id=${id} ref=${ta} rows="1" placeholder=${placeholder} value=${text}
        onInput=${(e) => setText(e.target.value)} onKeyDown=${onKey}></textarea>`}
      ${onAttach && !voiceBusy && html`<button class="icon-btn attach-button" aria-label="Attach files" title="Attach files" onClick=${() => picker.current.click()}>${Icon.plus}</button>
        <input ref=${picker} type="file" multiple hidden onChange=${(e) => { attach(e.target.files); e.target.value = ''; }} />`}
      ${voice && !voiceBusy && html`<button class="icon-btn" aria-label="Voice" title="Record a voice note" onClick=${recorder.start}>${Icon.mic}</button>`}
      ${busy && !ready && !voiceBusy && onStop
        ? html`<button class="icon-btn hot" aria-label="Stop" title="Stop" onClick=${() => Promise.resolve(onStop()).catch((e) => toast(e.message))}>${Icon.stop}</button>`
        : (ready || voiceBusy) && html`<button class="icon-btn hot" aria-label="Send" disabled=${recording ? !recorder.heard : (!ready || sending || voiceBusy || uploading > 0)} onClick=${send}>${Icon.send}</button>`}
    </div>
  </div>`;
}

function Message({ m }) {
  const files = m.files || [];
  const content = useMemo(() => md(m.text), [m.text]);
  const recording = m.audio || (m.voice && files.some(isAudio) ? {url:`/api/chats/${m.project}/files/${encodeURIComponent(files.find(isAudio))}`,name:files.find(isAudio),...m.voice} : null);
  if (m.role === 'you') {
    return html`<div class="msg you"><div class="stack">
      ${recording && html`<${VoiceNote} key="recording" src=${recording.url} peaks=${recording.peaks} seconds=${recording.seconds} transcript=${m.text} pending=${m.delivery==='listening'} failed=${m.delivery==='voice-failed'} onRetry=${m.retryVoice} failure=${m.voiceError}/>`}
      ${files.filter(f=>!recording || f!==recording.name).map((f) => {
        const url = `/api/chats/${m.project}/files/${encodeURIComponent(f)}`;
        return isAudio(f) ? html`<${VoiceNote} src=${url} peaks=${m.voice?.peaks} seconds=${m.voice?.seconds}/>` : isImage(f)
          ? html`<a class="img" href=${url} target="_blank"><img src=${url} alt=${f} loading="lazy" /></a>`
          : html`<a class="filechip" href=${url} target="_blank">${Icon.file}${f}</a>`;
      })}
      ${m.text && !recording && html`<div class="bubble">${content}</div>`}
    </div></div>`;
  }
  return html`<div class=${'msg' + (m.error ? ' err' : '')}><div class="txt">
    ${m.run_title && html`<div class="from-run">${m.run_status === 'done' ? '✓' : '✕'} ${m.run_title}</div>`}
    ${content}</div></div>`;
}

// Text arrives in bursts; reveal it at a steady pace that catches up with each burst.
function useSmooth(text) {
  const [shown, setShown] = useState(0), target = useRef(text);
  target.current = text;
  useEffect(() => { let frame; const tick = () => { setShown((n) => { const t = target.current.length; return n > t ? 0 : n + Math.ceil((t - n) / 6); }); frame = requestAnimationFrame(tick); }; frame = requestAnimationFrame(tick); return () => cancelAnimationFrame(frame); }, []);
  return text.slice(0, shown);
}

function Live({ stream, activity }) {
  const text = useSmooth(stream || '');
  // The status line sticks to the bottom of the view, so it stays visible under a long answer.
  const status = html`<div class="typing" role="status"><span class="pulse"></span><span class="ellipsis">${activity || (stream ? 'Writing…' : 'Thinking…')}</span></div>`;
  return stream ? html`<div class="msg"><div class="txt live">${md(text)}</div></div>${status}` : status;
}

// ----------------------------------------------------------------- approvals

const VERB = { email: 'Send', post: 'Post', merge: 'Merge', spend: 'Approve spend', other: 'Approve' };
const KIND = { email: 'Email', post: 'Post', merge: 'Merge & ship', spend: 'Spend', other: 'Needs your OK' };

function ApprovalCard({ a, project, toast }) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(a.body);
  const [busy, setBusy] = useState(false);
  const decide = async (decision) => {
    setBusy(true);
    try {
      await api(`/api/approvals/${a.id}`, { json: { decision, body: editing ? body : undefined } });
      toast(decision === 'approve' ? `Approved. ${project ? project.name : 'Ken'} is on it.` : 'Skipped.');
    } catch (e) { toast(e.message); setBusy(false); }
  };
  return html`<article class="card approval">
    <div class="row meta"><${Chip} project=${project} /><span>${KIND[a.kind] || KIND.other}</span><span style="margin-left:auto">${clock(a.created)}</span></div>
    <div class="title">${a.title}</div>
    ${editing
      ? html`<label class="sr" for=${'edit-' + a.id}>Edit before approving</label><textarea id=${'edit-' + a.id} value=${body} onInput=${(e) => setBody(e.target.value)}></textarea>`
      : a.body && html`<div class="well">${md(a.body)}</div>`}
    ${a.next && !editing && html`<div class="meta">Then: ${a.next}</div>`}
    <div class="row">
      <button class="btn primary" disabled=${busy} onClick=${() => decide('approve')}>${VERB[a.kind] || 'Approve'}</button>
      ${editing
        ? html`<button class="btn" disabled=${busy} onClick=${() => { setEditing(false); setBody(a.body); }}>Cancel edit</button>`
        : html`<button class="btn" disabled=${busy} onClick=${() => setEditing(true)}>Edit</button>`}
      <button class="btn ghost" disabled=${busy} onClick=${() => decide('skip')}>Skip</button>
    </div>
  </article>`;
}

// ---------------------------------------------------------------------- runs

const elapsed = (run) => { const a = ago(run.started || run.created); return a === 'now' ? '<1m' : a; };
const stopRun = async (id, toast) => { try { await api(`/api/runs/${id}/stop`, { json: {} }); toast('Stopping…'); } catch (e) { toast(e.message); } };

function RunCard({ run, project, openRun, toast, showProject = true }) {
  return html`<div class="card run">
    ${showProject && html`<div class="row meta small"><span style=${{ color: project ? project.color : 'inherit', fontWeight: 600 }}>${project ? project.name : run.project}</span><span style="margin-left:auto">${elapsed(run)}</span></div>`}
    <div class="row"><span class="dot go"></span><span class="title" style="flex:1">${run.title}</span>${!showProject && html`<span class="meta">${elapsed(run)}</span>`}</div>
    <div class="mono ellipsis">${run.status === 'queued' ? 'waiting for a free slot' : run.activity || 'working'}</div>
    <div class="bar"><i></i></div>
    <div class="row">
      <button class="btn sm" onClick=${() => openRun(run.id)}>Watch live</button>
      <button class="btn sm ghost" onClick=${() => stopRun(run.id, toast)}>Stop</button>
    </div>
  </div>`;
}

function DoneItem({ run, project, openRun }) {
  const mark = run.status === 'done' ? html`<span class="ok">✓</span>` : run.status === 'stopped' ? html`<span class="meta">■</span>` : html`<span class="bad">✕</span>`;
  return html`<button class="done-item" onClick=${() => openRun(run.id)}>
    ${mark}
    <span class="t"><span>${run.title}</span><span class="s ellipsis">${project ? project.name + ' · ' : ''}${firstLine(run.summary)}</span></span>
    <span class="meta">${ago(run.finished || run.created)}</span>
  </button>`;
}

function RunDrawer({ rid, projects, tick, onClose, toast }) {
  const [run, setRun] = useState(null);
  const logRef = useRef(null);
  useEffect(() => { api(`/api/runs/${rid}`).then(setRun).catch((e) => toast(e.message)); }, [rid, tick]);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [run && run.events.length]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);
  const project = run && projects.find((p) => p.id === run.project);
  const active = run && (run.status === 'running' || run.status === 'queued');
  const steps = run ? run.events.filter((e) => e.type === 'run.tool' || (e.type === 'run.text' && (active || e.text.trim() !== (run.summary || '').trim()))) : [];
  return html`<div class="scrim" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <aside class="drawer" role="dialog" aria-label="Run">
      <header>
        <div style="flex:1;min-width:0">
          <div class="row meta"><${Chip} project=${project} /><span>${run ? (active ? 'running · ' + elapsed(run) : run.status) : ''}</span></div>
          <h2>${run ? run.title : 'Loading…'}</h2>
        </div>
        <button class="icon-btn" aria-label="Close" onClick=${onClose}>${Icon.close}</button>
      </header>
      <div class="log" ref=${logRef}>
        ${steps.map((e) => (e.type === 'run.tool' ? html`<div class="mono">› ${e.text}</div>` : html`<div class="text">${md(e.text)}</div>`))}
        ${active && html`<${Live} activity=${run.activity} />`}
        ${run && !active && run.summary && html`<div class="card result"><div class="meta">Result</div><div>${md(run.summary)}</div></div>`}
      </div>
      ${active && html`<footer><button class="btn" onClick=${() => stopRun(rid, toast)}>Stop this run</button></footer>`}
    </aside>
  </div>`;
}

// -------------------------------------------------------------------- modals

function Modal({ title, children, onClose }) {
  const box = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    const focusable = () => [...box.current.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), textarea, select:not(:disabled)')];
    const first = focusable()[0];
    if (first) first.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Tab') {
        const items = focusable(), first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    addEventListener('keydown', onKey);
    return () => { removeEventListener('keydown', onKey); if (previous && previous.isConnected) previous.focus(); };
  }, []);
  return html`<div class="scrim center" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div ref=${box} class="modal" role="dialog" aria-modal="true" aria-label=${title}><h2>${title}</h2>${children}</div>
  </div>`;
}

// ---------------------------------------------------------------- desktop UI
function Glyph(name) {
  const paths = {
    home: 'M3 10 12 3 21 10M5 9v11h5v-6h4v6h5V9',
    chat: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z',
    folder: 'M3 7V5h7l2 3h9v12H3V7Z',
    browser: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3c5 6 5 12 0 18-5-6-5-12 0-18Z',
    settings: 'm9 3-1 3-3 1-2 5 2 5 3 1 1 3h6l1-3 3-1 2-5-2-5-3-1-1-3H9ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
    arrow: 'M5 12h14m-6-6 6 6-6 6',
    file: 'M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h6',
  };
  return html`<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${paths[name] || paths.file}/></svg>`;
}
const fileUrl = (f) => `/api/chats/${f.project}/files/${encodeURIComponent(f.name)}`;
const isVideo = (name) => /\.(mp4|webm|mov)$/i.test(name);
const isAudio = (name) => /\.(mp3|wav|m4a|ogg|oga|opus|flac|aac|aiff?)$/i.test(name) || /^voice-.*\.webm$/i.test(name);
const isText = (name) => /\.(txt|md|csv|json|log|py|js|css|html)$/i.test(name);
const bytes = (n) => n < 1024 ? n + ' B' : n < 1024 * 1024 ? Math.round(n / 1024) + ' KB' : (n / 1024 / 1024).toFixed(1) + ' MB';

function PreviewModal({ file, onClose, toast }) {
  const [text, setText] = useState(null);
  useEffect(() => {
    let alive = true;
    if (isText(file.name)) api(`/api/chats/${file.project}/preview/${encodeURIComponent(file.name)}`).then((r) => alive && setText(r)).catch((e) => alive && setText({ text: e.message }));
    return () => { alive = false; };
  }, [file]);
  return html`<${Modal} title=${file.name} onClose=${onClose}>
    <div class="file-preview">${isImage(file.name) ? html`<img src=${fileUrl(file)} alt=${file.name} />` : isVideo(file.name) ? html`<video src=${fileUrl(file)} controls></video>` : isAudio(file.name) ? html`<audio src=${fileUrl(file)} controls></audio>` : isText(file.name) ? html`<pre>${text ? text.text : 'Loading preview…'}</pre>${text && text.truncated && html`<p class="meta">Preview shortened. Download the full file below.</p>`}` : html`<div class="document-art">${Glyph('file')}</div><p>Download this file to open it in your usual app.</p>`}</div>
    <div class="row"><a class="btn primary download-btn" href=${fileUrl(file)} download=${file.name}>Download</a><button class="btn" onClick=${() => { onClose(); location.hash = file.project === 'home' ? '#/conversations' : '#/p/' + file.project; }}>Talk about this</button><button class="btn ghost" onClick=${onClose}>Close</button></div>
  <//>`;
}

function ChatPage({ pid, chat, data, files, stream, activity, voice, toast, onPreview, draft, onDraftUsed, openRun, projects, onSend, onVoice, choices, onChoice }) {
  const thread = useRef(null);
  const following = useRef(true);
  const [dragging, setDragging] = useState(false);
  // Drop files anywhere in the window, or paste them, to attach to the message.
  useEffect(() => {
    let depth = 0;
    const files = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
    const enter = (e) => { if (files(e)) { e.preventDefault(); depth++; setDragging(true); } };
    const over = (e) => { if (files(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } };
    const leave = (e) => { if (files(e) && --depth <= 0) { depth = 0; setDragging(false); } };
    const drop = (e) => { if (!files(e)) return; e.preventDefault(); depth = 0; setDragging(false); if (e.dataTransfer.files.length) setDropped([...e.dataTransfer.files]); };
    const paste = (e) => { const list = [...(e.clipboardData?.files || [])]; if (list.length) { e.preventDefault(); setDropped(list); } };
    const events = { dragenter: enter, dragover: over, dragleave: leave, drop, paste };
    for (const [name, fn] of Object.entries(events)) addEventListener(name, fn);
    return () => { for (const [name, fn] of Object.entries(events)) removeEventListener(name, fn); };
  }, []);
  const [dropped, setDropped] = useState(null);
  const [recordRequest, setRecordRequest] = useState(0);
  const messages = chat ? chat.messages : [];
  const clearedAt = chat ? chat.cleared_at || 0 : 0;
  const busy = chat && chat.busy;
  const project = projects.find((p) => p.id === pid);
  const byId = Object.fromEntries(projects.map((p) => [p.id, p]));
  const approvals = data ? data.approvals.filter((a) => a.project === pid) : [];
  const runs = data ? data.running.filter((r) => r.project === pid) : [];
  const timeline = [
    ...messages.map((m) => ({ t: m.ts, key: 'm' + (m.client_id || m.id), mine: m.role === 'you', el: html`<${Message} m=${m}/>${m.delivery && !m.audio && html`<div class="delivery" role="status">${m.delivery === 'failed' ? html`Couldn’t send. <button class="text-action" onClick=${() => onSend(m.text, m.files, m.client_id).catch((e) => toast(e.message))}>Retry</button>` : m.delivery === 'sending' ? 'Sending…' : 'Waiting for Ken…'}</div>`}` })),
    ...approvals.map((a) => ({ t: a.created, key: 'a' + a.id, el: html`<${ApprovalCard} a=${a} project=${byId[a.project]} toast=${toast}/>` })),
    ...runs.map((r) => ({ t: r.created, key: 'r' + r.id, el: html`<${RunCard} run=${r} project=${byId[r.project]} openRun=${openRun} toast=${toast}/>` })),
    ...files.filter((f) => f.mtime > clearedAt && !ATTACHED.has(f.name) && !messages.some((m) => (m.files || []).includes(f.name))).map((f) => ({ t: f.mtime, key: 'f' + f.name, el: html`<button class="chat-file" onClick=${() => onPreview(f)}>${Icon.file}<span><b>${f.name}</b><small>${bytes(f.size)} · Open preview</small></span><span class="file-arrow">↗</span></button>` })),
  ].sort((a, b) => a.t - b.t);
  // Opening a chat shows the latest messages. After you send, your question moves to the
  // top and Ken's answer grows below it; the view never chases the end of a long answer.
  const pin = useRef(null), spacer = useRef(null);
  const lastMine = () => [...timeline].reverse().find((item) => item.mine)?.key;
  const pinNext = () => { pin.current = { after: lastMine() }; };
  useLayoutEffect(() => {
    const box = thread.current;
    if (!box) return;
    if (pin.current && !pin.current.key && lastMine() !== pin.current.after) pin.current = { key: lastMine() };
    const el = pin.current?.key && box.querySelector(`[data-key="${CSS.escape(pin.current.key)}"]`);
    if (!el) { if (following.current) box.scrollTop = box.scrollHeight; return; }
    const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 16;
    const below = spacer.current.getBoundingClientRect().top - el.getBoundingClientRect().top;
    spacer.current.style.height = Math.max(0, box.clientHeight - below - 56) + 'px';
    if (!pin.current.placed) { pin.current.placed = true; box.scrollTo({ top, behavior: 'smooth' }); }
  });
  return html`<section class="conversation-body" aria-label="Conversation"
>
    <div class="scroll" ref=${thread} onScroll=${() => { const el = thread.current; following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; }}>
      <div class="thread conversation-thread">
        ${!chat ? html`<p class="quiet-copy" role="status">Loading your conversation…</p>` : !messages.length && !busy && html`<div class="chat-welcome"><p class="hello">${project ? 'Let’s pick up ' + project.name + '.' : 'Hey, I’m Ken. Your assistant.'}</p><p>${project ? 'Tell me what you’d like to work on.' : clearedAt ? 'A fresh thread. What would you like to work on?' : 'Tell me what you do for work and what you’d like off your plate.'}</p>${voice && !project && !clearedAt && html`<button class="text-action voice-invite" onClick=${() => setRecordRequest((n) => n + 1)}>${Icon.mic} Record a voice note</button><p class="welcome-hint">Or just type below.</p>`}</div>`}
        ${timeline.map((item) => html`<div key=${item.key} data-key=${item.key}>${item.el}</div>`)}
        ${busy && (stream || activity || messages[messages.length - 1]?.role !== 'ken') && html`<${Live} stream=${stream} activity=${activity}/>`}
        <div ref=${spacer} aria-hidden="true"></div>
      </div>
    </div>
    <div class="chat-bottom">
      ${choices && html`<div class="model-choices">${choices.map(c=>html`<button class="btn sm" onClick=${()=>onChoice(c.text)}>${c.label}</button>`)}</div>`}
      <${Composer} placeholder="Tell Ken what you need…" draft=${draft} onDraftUsed=${onDraftUsed} storageKey=${pid} recordRequest=${recordRequest} dropped=${dropped} voice=${voice} toast=${toast} busy=${busy}
        onVoice=${(...args)=>{pinNext();onVoice(...args);}} onAttach=${(list) => upload(pid, list, true)} onStop=${() => api(`/api/chats/${pid}/stop`, { json: {} })}
        onSend=${async (text, files) => { pinNext(); await onSend(text, files); }}/>
    </div>
    ${dragging && html`<div class="dropzone">Drop files into your message</div>`}
  </section>`;
}

// claude-sonnet-4-5-20250929 → Sonnet 4.5
const modelName = (id) => { const [family, ...version] = id.replace(/^claude-/, '').replace(/-\d{8}$/, '').split('-'); return family[0].toUpperCase() + family.slice(1) + (version.length ? ' ' + version.join('.') : ''); };

function ChatHeader({ onClear, clearing, model, onModel, status, onRetry, awake, onAwake }) {
  return html`<header class="chat-header"><div class="ken-brand"><img src="/ken.svg" alt=""/><span>Ken</span>${status && html`<span class="ken-status" role="status"><i></i>${status}${onRetry && html` · <button class="text-action" onClick=${onRetry}>Try again</button>`}</span>`}</div><div class="header-actions">${awake !== null && html`<button class=${'coffee' + (awake ? ' on' : '')} aria-pressed=${awake} aria-label="Keep your Mac awake" data-tip=${awake ? 'Coffee’s on — your Mac stays awake so Ken can keep working while you’re away. You can still lock your screen (⌃⌘Q); closing the lid puts it to sleep. Click for decaf.' : 'Give your Mac a coffee — it stays awake so Ken can keep working while you’re away, even with the screen locked. Off by default.'} onClick=${onAwake}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path class="steam" d="M8.5 7.5c-.8-1 .8-1.7 0-2.8M12 7.5c-.8-1 .8-1.7 0-2.8"/><path d="M4.5 10h12v4a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5Z"/><path d="M16.5 11.5h1.2a2.3 2.3 0 0 1 0 4.6h-1.5"/></svg></button>`}${model?.choices?.length > 0 && html`<select class="model-select" aria-label="Model" title="Model" value=${model.current || ''} onChange=${(e) => onModel(e.target.value)}>${!model.current && html`<option value="">Default model</option>`}${model.choices.map((m) => html`<option value=${m}>${modelName(m)}</option>`)}</select>`}<button class="clear-button" disabled=${clearing} title="Start a fresh conversation; keep memory and files" onClick=${onClear}>Clear</button></div></header>`;
}

function App() {
  const [route, setRoute] = useState(parseHash());
  const [today, setToday] = useState(null);
  const [chat, setChat] = useState(null);
  const [stream, setStream] = useState({});
  const [activity, setActivity] = useState({});
  const [meta, setMeta] = useState({});
  const [library, setLibrary] = useState([]);
  const [runId, setRunId] = useState(null);
  const [tick, setTick] = useState(0);
  const [choices,setChoices]=useState(null);
  const [model,setModel]=useState(null);
  const loadModel=()=>api('/api/command',{json:{text:'/model'}}).then(setModel).catch(()=>{});
  const [awake,setAwake]=useState(null);
  const loadAwake=()=>api('/api/command',{json:{text:'/awake'}}).then((r)=>setAwake(r.awake)).catch(()=>{});
  const [preview, setPreview] = useState(null);
  const [draft, setDraft] = useState(null);
  const [outbox, setOutbox] = useState([]);
  const [clearing, setClearing] = useState(false);
  const received = useRef(new Set());
  const inFlight = useRef(new Set());
  const generation = useRef(0);
  const [toastMsg, setToastMsg] = useState('');
  const [online, setOnline] = useState(false);
  // Only mention the connection if it stays down; quick reconnects pass unnoticed.
  const [offline, setOffline] = useState(false);
  useEffect(() => { if (online) { setOffline(false); return; } const t = setTimeout(() => setOffline(true), 1200); return () => clearTimeout(t); }, [online]);
  const [loadError, setLoadError] = useState('');
  const pid = route.pid || 'home';
  const pidRef = useRef(pid); pidRef.current = pid;
  const warm = (target) => api(`/api/chats/${target}/warm`, {json:{}}).catch(()=>{});
  useEffect(()=>{warm(pid);},[pid]);
  const toast = useCallback((m) => setToastMsg(m), []);
  useEffect(() => { if (!toastMsg) return; const t = setTimeout(() => setToastMsg(''), 6000); return () => clearTimeout(t); }, [toastMsg]);
  const remember = (messages) => {
    for (const m of messages) if (m.client_id) received.current.add(m.client_id);
    setOutbox((all) => all.filter((m) => !received.current.has(m.client_id)));
  };
  const accept = (message) => {
    if (!message) return;
    generation.current++;
    remember([message]);
    if (message.project === pidRef.current) setChat((c) => c && c.pid === message.project
      ? { ...c, messages: c.messages.some((m) => m.id === message.id) ? c.messages : [...c.messages, message].sort((a,b) => a.id-b.id) }
      : c);
  };
  const load = useCallback(({extras = true} = {}) => {
    const current = pidRef.current, version = ++generation.current;
    // Chat never waits on a filesystem scan or dashboard data.
    api(`/api/chats/${current}`).then((conversation) => {
      if (pidRef.current === current && generation.current === version) {
        remember(conversation.messages); setChat({ pid: current, ...conversation }); setLoadError('');
      }
    }).catch((e) => { if (pidRef.current === current && generation.current === version) setLoadError(e.message); });
    if(extras){api('/api/today').then(setToday).catch(() => {});api('/api/library').then(setLibrary).catch(() => {});}
  }, []);
  const loadMeta = () => api('/api/meta').then(setMeta).catch((e) => toast(e.message));
  useEffect(() => {
    const onHash = () => setRoute(parseHash()); addEventListener('hashchange', onHash);
    if (window.kenDesktop) document.documentElement.dataset.desktop = window.kenDesktop.platform;
    loadMeta(); loadModel(); loadAwake();
    const refresh = setInterval(load, 15000);
    return () => { removeEventListener('hashchange', onHash); clearInterval(refresh); };
  }, []);
  useEffect(() => { load(); }, [pid]);
  useEffect(() => {
    let ws, timer, pending, closed = false, rev = null, retry = 0;
    const refresh = () => { clearTimeout(pending); pending = setTimeout(() => { load(); setTick((t) => t + 1); }, 100); };
    const connect = () => {
      ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/events');
      ws.onopen = () => { setOnline(true); retry = 0; refresh(); };
      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data), project = ev.project;
        if (ev.type === 'hello') { if (rev === null) rev = ev.rev; else if (ev.rev !== rev) location.reload(); return; }
        if (ev.type === 'chat.delta') { setStream((s) => ({ ...s, [project]: ev.text })); return; }
        if (ev.type === 'chat.tool') { setActivity((a) => ({ ...a, [project]: ev.text })); return; }
        if (ev.type === 'chat.busy') {
          generation.current++;
          setChat((c) => c && c.pid === project ? { ...c, busy: ev.busy } : c);
          if (!ev.busy) { setStream((s) => ({ ...s, [project]: '' })); setActivity((a) => ({ ...a, [project]: '' })); }
          return;
        }
        if (ev.type === 'message') {
          accept(ev);
          if (ev.role === 'ken') { setStream((s) => ({ ...s, [project]: '' })); setActivity((a) => ({ ...a, [project]: '' })); }
          if(ev.role==='you')return;
        }
        if (ev.type === 'chat.reset') {
          generation.current++;
          setStream((s) => ({ ...s, [project]: '' })); setActivity((a) => ({ ...a, [project]: '' }));
        }
        refresh();
      };
      ws.onclose = () => { setOnline(false); if (!closed) timer = setTimeout(connect, Math.min(8000, 500 * 2 ** retry++)); };
    };
    connect();
    return () => { closed = true; clearTimeout(timer); clearTimeout(pending); if (ws) ws.close(); };
  }, []);
  const send = async (text, files, retryId, target = pid) => {
    if (!retryId && !files.length && await command(text)) return;
    const client_id = retryId || crypto.randomUUID();
    if (inFlight.current.has(client_id)) return;
    inFlight.current.add(client_id);
    const optimistic = { id: client_id, client_id, ts: Date.now()/1000, project: target, role: 'you', text, files, delivery: 'sending' };
    setOutbox((all) => [...all.filter((m) => m.client_id !== client_id), optimistic]);
    try {
      const result = await api(`/api/chats/${target}/messages`, { json: { text, files, client_id }, timeout: 15000 });
      if (result.message) accept(result.message);
      else setOutbox((all) => all.map((m) => m.client_id === client_id ? { ...m, delivery: 'accepted' } : m));
      load({extras:false});
    } catch (e) {
      if (!received.current.has(client_id)) {
        setOutbox((all) => all.map((m) => m.client_id === client_id ? { ...m, delivery: 'failed' } : m));
        throw new Error('Message not confirmed. Use Retry beside it to send again.');
      }
    } finally { inFlight.current.delete(client_id); }
  };
  const voiceDrafts = () => {try{return JSON.parse(sessionStorage.getItem('ken-voice-outbox')||'{}');}catch{return {};}};
  const saveVoiceDraft = (id,data) => {const saved=voiceDrafts();if(data)saved[id]=data;else delete saved[id];sessionStorage.setItem('ken-voice-outbox',JSON.stringify(saved));};
  const sendVoice = (blob, peaks=[], seconds=0, restore=null) => {
    const target=restore?.project||pid, id=restore?.id||crypto.randomUUID(), audio={blob,peaks:waveformPeaks(peaks),seconds,url:URL.createObjectURL(blob),name:restore?.name||null,transcript:restore?.transcript??null};
    const persist=()=>{if(audio.name)saveVoiceDraft(id,{id,project:target,name:audio.name,peaks:audio.peaks,seconds:audio.seconds,transcript:audio.transcript});};
    const retryVoice = async () => {
      if(inFlight.current.has(id))return;
      inFlight.current.add(id);
      const note={id,client_id:id,ts:Date.now()/1000,project:target,role:'you',text:audio.transcript||'',files:audio.name?[audio.name]:[],audio,retryVoice,delivery:'listening'};
      setOutbox(all=>[...all.filter(m=>m.client_id!==id),note]);
      try {
        // Saving a recording and listening to it are independent. Start both now.
        const results=await Promise.allSettled([
          (async()=>{
            if(!audio.name){const file=new File([blob],`voice-${id}.webm`,{type:blob.type});audio.name=(await upload(target,[file],true))[0];if(!audio.name)throw new Error('Couldn’t save this recording. Try again.');}
            persist();setOutbox(all=>all.map(m=>m.client_id===id?{...m,files:[audio.name]}:m));
          })(),
          (async()=>{
            if(audio.transcript===null){const result=await api('/api/voice',{body:blob,timeout:690000});if(!result.text)throw new Error('No speech detected');audio.transcript=result.text;persist();}
            setOutbox(all=>all.map(m=>m.client_id===id?{...m,text:audio.transcript}:m));
          })(),
        ]);
        for(const result of results)if(result.status==='rejected')throw result.reason;
        const result=await api(`/api/chats/${target}/messages`,{json:{text:audio.transcript,files:[audio.name],client_id:id,voice:{seconds:audio.seconds,peaks:audio.peaks}},timeout:15000});
        accept(result.message);load({extras:false});saveVoiceDraft(id,null);
      } catch(e) {
        if(!received.current.has(id))setOutbox(all=>all.map(m=>m.client_id===id?{...m,delivery:'voice-failed',voiceError:e.message}:m));
      } finally {inFlight.current.delete(id);}
    };
    retryVoice();
  };
  const restoredVoice = useRef(false);
  useEffect(()=>{
    if(restoredVoice.current)return;restoredVoice.current=true;
    for(const note of Object.values(voiceDrafts())){
      fetch(`/api/chats/${note.project}/files/${encodeURIComponent(note.name)}`).then(r=>{if(!r.ok)throw Error();return r.blob();}).then(blob=>sendVoice(blob,note.peaks,note.seconds,note)).catch(()=>toast('A saved voice note could not be reopened. The original file is still in your chat folder.'));
    }
  },[]);
  const command = async (text) => {
    const value=text.trim().toLowerCase();
    if(value==='/new' || /^(clear (this |the )?(conversation|chat|thread)|start a (fresh|new) (conversation|chat|thread))[.!]?$/.test(value)){await clearThread();return true;}
    if(['/stop','stop','stop working'].includes(value)){await api(`/api/chats/${pid}/stop`,{json:{}});return true;}
    if(value.startsWith('/')){
      const result=await api('/api/command',{json:{text:text.trim()}});
      setChoices(result.choices?.length?result.choices.map(m=>({label:m,text:text.trim().split(' ')[0]+' '+m})):null);
      if(result.reply)toast(result.reply);
      if(value.startsWith('/model'))loadModel();
      if('awake' in result)setAwake(result.awake);
      return true;
    }
    return false;
  };
  const clearThread = async () => {
    if (clearing) return;
    if ((chat && chat.busy) || outbox.some((m) => m.project === pid && !['failed','voice-failed'].includes(m.delivery))) { toast('Stop or finish the reply before clearing this thread.'); return; }
    setClearing(true); generation.current++;
    try {
      const result = await api(`/api/chats/${pid}/reset`, { json: {} });
      sessionStorage.removeItem('ken-draft:' + pid); setDraft(null); setChoices(null);
      for(const note of Object.values(voiceDrafts()))if(note.project===pid)saveVoiceDraft(note.id,null);
      setOutbox((all) => all.filter((m) => m.project !== pid));
      if (pidRef.current === pid) setChat({ pid, ...result });
      setStream((s) => ({ ...s, [pid]: '' })); setActivity((a) => ({ ...a, [pid]: '' }));
      toast('Thread cleared. Your memory and files are kept.');
      warm(pid);
    } catch (e) { toast(e.message); }
    finally { setClearing(false); }
  };
  const projects = today ? today.projects : [];
  const visibleChat = chat && chat.pid === pid ? { ...chat, messages: [...chat.messages, ...outbox.filter((m) => m.project === pid && !chat.messages.some((saved) => saved.client_id === m.client_id))] } : null;
  return html`<div class="shell"><main class="main"><${ChatHeader} awake=${awake} onAwake=${()=>api('/api/command',{json:{text:awake?'/decaf':'/coffee'}}).then((r)=>{setAwake(r.awake);toast(r.awake?'☕ Coffee’s on — lock your screen anytime; Ken keeps working.':'Decaf — your Mac can sleep as usual.');}).catch((e)=>toast(e.message))} status=${loadError ? 'Can’t reach Ken' : offline ? 'Reconnecting…' : ''} onRetry=${loadError ? load : null} onClear=${clearThread} clearing=${clearing} model=${model} onModel=${(m)=>m&&api('/api/command',{json:{text:'/model '+m}}).then(loadModel).catch((e)=>toast(e.message))}/>
    <${ChatPage} key=${pid + ':' + (chat && chat.pid === pid ? chat.cleared_at || 0 : 0)} pid=${pid} chat=${visibleChat} data=${today} files=${library.filter((f) => f.project === pid)} stream=${stream[pid]} activity=${activity[pid]} voice=${meta.voice} toast=${toast} onPreview=${setPreview} draft=${pid === 'home' ? draft : null} onDraftUsed=${() => setDraft(null)} openRun=${setRunId} projects=${projects} onSend=${send} onVoice=${sendVoice} choices=${choices} onChoice=${(text)=>{setChoices(null);command(text).catch((e)=>toast(e.message));}}/>
  </main>
    ${runId && html`<${RunDrawer} rid=${runId} projects=${projects} tick=${tick} onClose=${() => setRunId(null)} toast=${toast}/>`}
    ${preview && html`<${PreviewModal} file=${preview} onClose=${() => setPreview(null)} toast=${toast}/>`}
    ${toastMsg && html`<div class="toast" role="status">${toastMsg}</div>`}
  </div>`;
}
// Hover cards for [data-tip]: one element on top of the page, shown after a short pause.
{
  const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; document.body.append(tip);
  let timer, target;
  addEventListener('mouseover', (e) => {
    const el = e.target.closest?.('[data-tip]');
    if (el === target) return;
    clearTimeout(timer); tip.hidden = true; target = el;
    if (!el) return;
    timer = setTimeout(() => {
      tip.textContent = el.dataset.tip; tip.hidden = false;
      const r = el.getBoundingClientRect(), w = tip.offsetWidth;
      tip.style.top = r.bottom + 8 + 'px'; tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.right - w)) + 'px';
    }, 300);
  });
  addEventListener('mousedown', () => { clearTimeout(timer); tip.hidden = true; });
}
render(html`<${App}/>`, document.getElementById('app'));
