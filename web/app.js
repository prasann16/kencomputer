// Ken: one conversation, with tools and settings available when needed.
import { html, render, useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from '/vendor/preact-htm.js';

// ------------------------------------------------------------------ helpers

// Never a silent failure: if anything on the page breaks, say so, with a way out. Plain DOM,
// so it still works when the part that broke is the app itself.
function showFailure(message) {
  let bar = document.getElementById('page-error');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'page-error';
    bar.setAttribute('role', 'alert');
    document.body.append(bar);
  }
  const reload = Object.assign(document.createElement('button'), { textContent: 'Reload', onclick: () => location.reload() });
  bar.replaceChildren(document.createTextNode(`Something went wrong: ${message || 'unknown error'}. `), reload);
}
addEventListener('error', (e) => showFailure(e.message));
// A mouse click (on a button, tab or link) shouldn't leave a focus ring behind (it shows up on the next key press, like ⌘\).
// Keyboard presses (detail 0) keep focus, so tabbing still shows where you are.
addEventListener('click', (e) => { if (e.detail > 0) e.target.closest?.('button, a, [role="tab"]')?.blur(); });
addEventListener('unhandledrejection', (e) => showFailure(e.reason?.message || String(e.reason)));

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
const parseHash = () => ({ pid: (location.hash.match(/^#\/p\/([a-z0-9-]+)/) || [])[1] || null, brain: location.hash === '#/brain' });

// Just enough markdown for chat: code blocks, tables, `code`, **bold**, links, • bullets. Never raw HTML.
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
    if (i % 2 === 0 || !/^\|[\s:|-]+\|$/.test(lines[1] || '')) return inline(part.replace(/^([ \t]*)[-*][ \t]+(?=\S)/gm, '$1• '));
    return html`<div class="table"><table><thead><tr>${cells(lines[0]).map((c) => html`<th>${inline(c)}</th>`)}</tr></thead>
      <tbody>${lines.slice(2).map((l) => html`<tr>${cells(l).map((c) => html`<td>${inline(c)}</td>`)}</tr>`)}</tbody></table></div>`;
  });
}
function md(text) {
  return String(text || '').split(/```[^\n]*\n?([\s\S]*?)```/g).map((block, i) =>
    i % 2 ? html`<pre><code>${block.replace(/\n$/, '')}</code></pre>` : prose(block));
}

// A notes file as a page: headings, bullet lists and paragraphs, with the same inline markdown.
function doc(text) {
  const blocks = [];
  let list = null, para = [];
  const flush = () => {
    if (para.length) blocks.push(html`<p>${inline(para.join(' '))}</p>`);
    if (list) blocks.push(html`<ul>${list.map((li) => html`<li>${inline(li)}</li>`)}</ul>`);
    para = []; list = null;
  };
  for (const line of String(text || '').split('\n')) {
    const h = line.match(/^(#{1,4})\s+(.*)/), li = line.match(/^\s*[-*]\s+(.*)/);
    if (h) { flush(); blocks.push(h[1].length === 1 ? html`<h2>${inline(h[2])}</h2>` : html`<h3>${inline(h[2])}</h3>`); }
    else if (li) { if (para.length) { const l = list; list = null; flush(); list = l; } (list = list || []).push(li[1]); }
    else if (!line.trim()) flush();
    else { if (list) flush(); para.push(line.trim()); }
  }
  flush();
  return blocks;
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

function Composer({ placeholder, onSend, onVoice, onAttach, voice, busy, onStop, toast, draft, dropped, onDraftUsed, storageKey }) {
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

function Message({ m, label }) {
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
  // A scheduled job's report says what it is, so Ken never seems to talk out of nowhere.
  return html`<div class=${'msg' + (m.error ? ' err' : '') + (m.job ? ' from-job' : '')}>
    ${label && html`<div class="job-label">${m.job.label}${m.job.time && html`<span> · ${m.job.time}</span>`}<a class="text-action" href="#/brain" title="Turn it off or change its time">Change</a></div>`}
    <div class="txt">
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

function Modal({ title, children, onClose, wide }) {
  const box = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    const focusable = () => [...box.current.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), textarea, select:not(:disabled)')];
    // Start in the first text field, if any; otherwise on the dialog itself, so no button wears a focus ring.
    const field = box.current.querySelector('input:not([type=checkbox]):not([type=time]):not(:disabled), textarea');
    (field || box.current).focus();
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
    <div ref=${box} tabindex="-1" class=${'modal' + (wide ? ' wide' : '')} role="dialog" aria-modal="true" aria-label=${title}><h2>${title}</h2>${children}</div>
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

function ChatPage({ pid, seenTs, chat, data, files, stream, activity, voice, toast, onPreview, draft, onDraftUsed, openRun, projects, onSend, onVoice, choices, onChoice, voiceSetup }) {
  const thread = useRef(null);
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
  const messages = chat ? chat.messages : [];
  const clearedAt = chat ? chat.cleared_at || 0 : 0;
  const busy = chat && chat.busy;
  const byId = Object.fromEntries(projects.map((p) => [p.id, p]));
  const approvals = data ? data.approvals.filter((a) => a.project === pid) : [];
  const runs = data ? data.running.filter((r) => r.project === pid) : [];
  const timeline = [
    ...messages.map((m, i) => ({ t: m.ts, key: 'm' + (m.client_id || m.id), mine: m.role === 'you', el: html`<${Message} m=${m} label=${m.job && messages[i - 1]?.job?.at !== m.job.at}/>${m.delivery && !m.audio && html`<div class="delivery" role="status">${m.delivery === 'failed' ? html`Couldn’t send. <button class="text-action" onClick=${() => onSend(m.text, m.files, m.client_id).catch((e) => toast(e.message))}>Retry</button>` : m.delivery === 'sending' ? 'Sending…' : 'Waiting for Ken…'}</div>`}` })),
    ...approvals.map((a) => ({ t: a.created, key: 'a' + a.id, el: html`<${ApprovalCard} a=${a} project=${byId[a.project]} toast=${toast}/>` })),
    ...runs.map((r) => ({ t: r.created, key: 'r' + r.id, el: html`<${RunCard} run=${r} project=${byId[r.project]} openRun=${openRun} toast=${toast}/>` })),
    ...files.filter((f) => f.mtime > clearedAt && !ATTACHED.has(f.name) && !messages.some((m) => (m.files || []).includes(f.name))).map((f) => ({ t: f.mtime, key: 'f' + f.name, el: html`<button class="chat-file" onClick=${() => onPreview(f)}>${Icon.file}<span><b>${f.name}</b><small>${bytes(f.size)} · Open preview</small></span><span class="file-arrow">↗</span></button>` })),
  ].sort((a, b) => a.t - b.t);
  // One continuous conversation, never a blank page. Opening a chat lands on the first unread
  // reply, or the end. Sending brings your message into view at the bottom. While Ken replies the
  // view follows, but stops once the start of the reply reaches the top, so you read it from the
  // start. Scroll up and it leaves you alone; "New reply" takes you back down.
  const following = useRef(true), placed = useRef(false), reply = useRef(null), seen = useRef({ n: 0, stream: '' });
  const [below, setBelow] = useState(false);
  const lastMine = () => [...timeline].reverse().find((item) => item.mine)?.key;
  const sent = () => { following.current = true; reply.current = { after: lastMine() }; setBelow(false); };
  const atEnd = (box) => box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const toEnd = () => { const box = thread.current; if (box) { following.current = true; setBelow(false); box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' }); } };
  useLayoutEffect(() => {
    const box = thread.current;
    if (!box || !chat) return;
    const offset = (el) => el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    if (!placed.current) {
      placed.current = true;
      const unread = seenTs && messages.find((m) => m.role === 'ken' && m.ts > seenTs);
      const el = unread && box.querySelector(`[data-key="${CSS.escape('m' + (unread.client_id || unread.id))}"]`);
      if (el && offset(el) + el.offsetHeight > box.clientHeight) { box.scrollTop = offset(el) - 16; following.current = atEnd(box); }
      else box.scrollTop = box.scrollHeight;
    } else if (following.current) {
      if (reply.current && !reply.current.key && lastMine() !== reply.current.after) reply.current = { key: lastMine() };
      const mine = reply.current?.key && box.querySelector(`[data-key="${CSS.escape(reply.current.key)}"]`);
      const stop = mine ? offset(mine) + mine.offsetHeight - 16 : Infinity; // where the reply starts
      const target = box.scrollHeight - box.clientHeight;
      if (target > stop) { box.scrollTop = Math.max(box.scrollTop, stop); following.current = false; }
      else box.scrollTop = target;
    }
    const grew = timeline.length !== seen.current.n || (stream || '') !== seen.current.stream;
    seen.current = { n: timeline.length, stream: stream || '' };
    if (grew && !atEnd(box) && !following.current && !below) setBelow(true);
  });
  // Scrolling up yourself always wins over following.
  const leave = () => { following.current = false; };
  return html`<section class="conversation-body" aria-label="Conversation"
>
    <div class="scroll" ref=${thread} onWheel=${(e) => e.deltaY < 0 && leave()} onTouchMove=${leave} onKeyDown=${(e) => ['ArrowUp', 'PageUp', 'Home'].includes(e.key) && leave()}
      onScroll=${() => { if (atEnd(thread.current)) setBelow(false); }}>
      <div class="thread conversation-thread">
        ${!chat && html`<p class="quiet-copy" role="status">Loading your conversation…</p>`}
        ${chat && !messages.length && !busy && html`<div class="chat-empty"><p>What can Ken take off your plate?</p>
          <div class="chat-examples">${EXAMPLES.map((t) => html`<button class="btn sm" onClick=${() => { sent(); onSend(t, []).catch((e) => toast(e.message)); }}>${t}</button>`)}</div></div>`}
        ${timeline.map((item) => html`<div key=${item.key} data-key=${item.key}>${item.el}</div>`)}
        ${busy && (stream || activity || messages[messages.length - 1]?.role !== 'ken') && html`<${Live} stream=${stream} activity=${activity}/>`}
      </div>
    </div>
    <div class="chat-bottom">
      ${below && html`<button class="new-below" onClick=${toEnd}>↓ New reply</button>`}
      ${voiceSetup?.state === 'downloading' && html`<div class="voice-setup" role="status">Getting voice ready${voiceSetup.progress != null ? ` · ${Math.round(voiceSetup.progress * 100)}%` : '…'}</div>`}
      ${choices && html`<div class="model-choices">${choices.map(c=>html`<button class="btn sm" onClick=${()=>onChoice(c.text)}>${c.label}</button>`)}</div>`}
      <${Composer} placeholder="Tell Ken what you need…" draft=${draft} onDraftUsed=${onDraftUsed} storageKey=${pid} dropped=${dropped} voice=${voice} toast=${toast} busy=${busy}
        onVoice=${(...args)=>{sent();onVoice(...args);}} onAttach=${(list) => upload(pid, list, true)} onStop=${() => api(`/api/chats/${pid}/stop`, { json: {} })}
        onSend=${async (text, files) => { sent(); await onSend(text, files); }}/>
    </div>
    ${dragging && html`<div class="dropzone">Drop files into your message</div>`}
  </section>`;
}

// claude-sonnet-4-5-20250929 → Sonnet 4.5
const modelName = (id) => { const [family, ...version] = id.replace(/^claude-/, '').replace(/-\d{8}$/, '').split('-'); return family[0].toUpperCase() + family.slice(1) + (version.length ? ' ' + version.join('.') : ''); };

function ChatHeader({ onClear, clearing, model, onModel, status, onRetry, awake, onAwake, connected, title, brain, onSettings, onProjectMenu }) {
  return html`<header class="chat-header"><nav class="top-tabs" aria-label="Ken">${title
      ? html`<span class="tab sel"><i class="proj-dot" style=${{background:title.color}}></i>${title.name}${onProjectMenu && html`<button class="proj-more" aria-label="Agent options" title="Agent options" onClick=${(e)=>onProjectMenu(e)}><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg></button>`}</span>`
      : html`<a class=${'tab' + (!brain ? ' sel' : '')} href="#/"><img src="/ken.svg" alt=""/>Chat</a><a class=${'tab' + (brain ? ' sel' : '')} href="#/brain">Brain</a>`}</nav>${status && html`<span class="ken-status" role="status"><i></i>${status}${onRetry && html` · <button class="text-action" onClick=${onRetry}>Try again</button>`}</span>`}${connected && !brain && html`<div class="header-actions">${awake !== null && html`<button class=${'coffee' + (awake ? ' on' : '')} aria-pressed=${awake} aria-label="Keep your Mac awake" data-tip=${awake ? 'Coffee’s on — your Mac stays awake so Ken can keep working while you’re away. You can still lock your screen (⌃⌘Q); closing the lid puts it to sleep. Click for decaf.' : 'Give your Mac a coffee — it stays awake so Ken can keep working while you’re away, even with the screen locked. Off by default.'} onClick=${onAwake}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path class="steam" d="M8.5 7.5c-.8-1 .8-1.7 0-2.8M12 7.5c-.8-1 .8-1.7 0-2.8"/><path d="M4.5 10h12v4a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5Z"/><path d="M16.5 11.5h1.2a2.3 2.3 0 0 1 0 4.6h-1.5"/></svg></button>`}${model?.choices?.length > 0 && html`<select class="model-select" aria-label="Model" title="Model" value=${model.current || ''} onChange=${(e) => onModel(e.target.value)}>${!model.current && html`<option value="">Default model</option>`}${model.choices.map((m) => html`<option value=${m}>${modelName(m)}</option>`)}</select>`}<button class="clear-button" disabled=${clearing} title="Start a fresh conversation; keep memory and files" onClick=${onClear}>Clear</button></div>`}<button class="settings-link" onClick=${onSettings}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></svg>Settings</button></header>`;
}

// First run: connect Claude with the same `claude setup-token` the installer runs.
function Connect({ onDone }) {
  const [step, setStep] = useState('start'), [url, setUrl] = useState(''), [value, setValue] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const go = async (body) => {
    setBusy(true); setError('');
    try { const r = await api('/api/setup', { json: body, timeout: 90000 }); if (r.url) { setUrl(r.url); setValue(''); setStep('code'); } if (r.error) setError(r.error); if (r.claude) onDone(r); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  // Approving in the browser usually connects Ken by itself, with no code to paste.
  useEffect(() => {
    if (step !== 'code') return;
    const timer = setInterval(async () => { try { const r = await api('/api/setup'); if (r.claude) { clearInterval(timer); onDone(r); } } catch {} }, 2000);
    return () => clearInterval(timer);
  }, [step]);
  const field = (placeholder) => html`<input class="connect-input" autofocus placeholder=${placeholder} value=${value} onInput=${(e) => setValue(e.target.value)} onKeyDown=${(e) => e.key === 'Enter' && value.trim() && !busy && go({ code: value })}/>`;
  return html`<div class="connect"><img src="/ken.svg" alt=""/><h1>Connect Claude</h1>
    ${step === 'start' && html`<p>Ken runs on your Claude subscription. Sign in once and you’re set.</p>
      <button class="btn primary" disabled=${busy} onClick=${() => go({ action: 'start' })}>${busy ? 'Opening Claude…' : 'Connect Claude'}</button>`}
    ${step === 'code' && html`<p>Approve Ken in your browser and Ken connects by itself. If Claude shows you a code instead, paste it here.</p>${field('Paste the code')}
      <button class="btn primary" disabled=${busy || !value.trim()} onClick=${() => go({ code: value })}>${busy ? 'Connecting…' : 'Connect'}</button>
      <a class="text-action" href=${url} target="_blank" rel="noopener">Browser didn’t open? Open the sign-in page</a>
      <button class="text-action" disabled=${busy} onClick=${() => go({ action: 'start' })}>Start over</button>`}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
  </div>`;
}

const APP_PASSWORD = { 'gmail.com': 'https://myaccount.google.com/apppasswords', 'googlemail.com': 'https://myaccount.google.com/apppasswords', 'icloud.com': 'https://account.apple.com/account/manage', 'me.com': 'https://account.apple.com/account/manage', 'mac.com': 'https://account.apple.com/account/manage' };

// What Ken can use, connected right here: Chrome, email, calendar, plus any tool Ken adds itself.
// A connected tool has a switch (does Ken use it); one that isn't connected has Connect.
function ToolList({ tools, onUse, onAddTool }) {
  const [state, setState] = useState(null), [open, setOpen] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [address, setAddress] = useState(''), [password, setPassword] = useState(''), [server, setServer] = useState(''), [url, setUrl] = useState(''), [want, setWant] = useState('');
  useEffect(() => { api('/api/connections').then(setState).catch((e) => setError(e.message)); }, []);
  const act = async (path, opts, after) => {
    setBusy(true); setError('');
    try { setState(await api(path, opts)); setOpen(''); after?.(); } catch (e) { setError(e.message); if (/server/.test(e.message)) setServer(server || ' '); }
    finally { setBusy(false); }
  };
  const domain = (address.split('@')[1] || '').toLowerCase();
  const help = APP_PASSWORD[domain];
  if (!state) return html`<p class="np-note">${error || 'Loading…'}</p>`;
  const row = (icon, title, text, right, body) => html`<div class="conn"><div class="conn-row"><span class="conn-icon">${icon}</span><div class="conn-text"><b>${title}</b><span>${text}</span></div><div class="conn-right">${right}</div></div>${body}</div>`;
  const on = (label) => html`<span class="conn-on"><i></i>${label}</span>`;
  const icon = (d) => html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d=${d}/></svg>`;
  const use = (t, connected, connect) => connected
    ? html`<input class="job-switch" type="checkbox" role="switch" aria-label=${`Ken uses ${TOOL_NAMES[t]}`} title="Ken uses it" checked=${tools.use.includes(t)} onChange=${(e) => onUse(t, e.target.checked)}/>`
    : connect;
  return html`<div class="tool-list">
    ${row(icon('M21 12a9 9 0 11-18 0 9 9 0 0118 0zM3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18'), 'Chrome', 'Browse websites, fill forms and post, using your signed-in Chrome.',
      use('browser', state.chrome, html`<button class="text-action" onClick=${() => { window.kenDesktop?.connectChrome?.(); setOpen('chrome'); }}>Connect</button>`),
      open === 'chrome' && !state.chrome && html`<p class="conn-steps">In the Chrome page that opened, switch on <b>Allow remote debugging</b>. The first time Ken uses Chrome, click <b>Allow</b>. Needs Chrome 144 or newer.</p>`)}
    ${row(icon('M4 6h16v12H4zM4 7l8 6 8-6'), 'Email', 'Search, read and draft replies. Sends only when you say so.',
      use('email', state.emails.length > 0, open !== 'email' && html`<button class="text-action" onClick=${() => { setOpen('email'); setError(''); setAddress(''); setServer(''); }}>Connect</button>`),
      html`${state.emails.length > 0 && html`<div class="conn-list">${state.emails.map((e) => html`<div>${on(e.address)}<button class="text-action" disabled=${busy} onClick=${() => act(`/api/connections/email?address=${encodeURIComponent(e.address)}`, { method: 'DELETE' })}>Disconnect</button></div>`)}${open !== 'email' && html`<div><button class="text-action" onClick=${() => { setOpen('email'); setError(''); setAddress(''); setServer(''); }}>Add another</button></div>`}</div>`}
      ${open === 'email' && html`<div class="conn-form">
        <input class="np-input" autofocus placeholder="you@gmail.com" value=${address} onInput=${(e) => setAddress(e.target.value)}/>
        <input class="np-input" type="password" placeholder="App password" value=${password} onInput=${(e) => setPassword(e.target.value)}/>
        ${server && html`<input class="np-input" placeholder="Mail server, e.g. imap.gmail.com" value=${server.trim()} onInput=${(e) => setServer(e.target.value)}/>`}
        <p class="conn-steps">Use an app password, not your normal one. ${help ? html`<a class="text-action" href=${help} target="_blank" rel="noopener">Create one</a>` : 'Your email provider’s security settings can create one.'} ${domain.match(/gmail/) ? '(Google asks you to turn on 2-Step Verification first.)' : ''}</p>
        <div class="row np-actions"><button class="btn ghost" onClick=${() => setOpen('')}>Cancel</button><button class="btn primary" disabled=${busy || !address || !password} onClick=${() => act('/api/connections/email', { json: { address, password, imap_host: server.trim() } }, () => setPassword(''))}>${busy ? 'Checking…' : 'Connect'}</button></div>
      </div>`}`)}
    ${row(icon('M4 6h16v14H4zM4 10h16M9 3v4M15 3v4'), 'Calendar', 'Ken reads your schedule for briefs and planning. Read-only.',
      use('calendar', state.calendars.length > 0, open !== 'calendar' && html`<button class="text-action" onClick=${() => { setOpen('calendar'); setError(''); }}>Connect</button>`),
      html`${state.calendars.length > 0 && html`<div class="conn-list">${state.calendars.map((c, i) => html`<div>${on(c.name)}<button class="text-action" disabled=${busy} onClick=${() => act(`/api/connections/calendar?index=${i}`, { method: 'DELETE' })}>Remove</button></div>`)}${open !== 'calendar' && html`<div><button class="text-action" onClick=${() => { setOpen('calendar'); setError(''); }}>Add another</button></div>`}</div>`}
      ${open === 'calendar' && html`<div class="conn-form">
        <input class="np-input" autofocus placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" value=${url} onInput=${(e) => setUrl(e.target.value)}/>
        <p class="conn-steps">In Google Calendar: <b>Settings</b> → your calendar → <b>Integrate calendar</b> → copy <b>Secret address in iCal format</b>. Apple Calendar and Outlook have a similar private link.</p>
        <div class="row np-actions"><button class="btn ghost" onClick=${() => setOpen('')}>Cancel</button><button class="btn primary" disabled=${busy || !url.trim()} onClick=${() => act('/api/connections/calendar', { json: { url } }, () => setUrl(''))}>${busy ? 'Checking…' : 'Add calendar'}</button></div>
      </div>`}`)}    ${Object.keys(tools.mcp).map((t) => row(icon('M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z'), t, 'Added by Ken.', on('On')))}
    ${open === 'add' ? html`<div class="conn-form tool-add"><input class="np-input" autofocus placeholder="Stripe, GitHub, Notion…" value=${want} onInput=${(e) => setWant(e.target.value)} onKeyDown=${(e) => e.key === 'Enter' && want.trim() && onAddTool(want.trim())}/>
        <p class="conn-steps">Ken sets it up in the chat: an MCP server, a command-line tool or an API, whichever works best, and asks for anything it needs.</p>
        <div class="row np-actions"><button class="btn ghost" onClick=${() => setOpen('')}>Cancel</button><button class="btn primary" disabled=${!want.trim()} onClick=${() => onAddTool(want.trim())}>Ask Ken</button></div></div>`
      : html`<button class="text-action tool-add-btn" onClick=${() => { setOpen('add'); setWant(''); }}>+ Add a tool</button>`}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
  </div>`;
}

const BOT_TOKEN = /\d{6,}:[A-Za-z0-9_-]{30,}/;  // a BotFather code, alone or inside its whole message

// Ken on your phone, wherever it's set up (first run, Settings): make a bot with BotFather on the
// phone, then paste its code here. An iPhone's copy is already on this Mac's clipboard, and Ken
// picks it up by itself.
function PhoneSteps({ toast, onPaired }) {
  const [phone, setPhone] = useState(null);
  const [botToken, setBotToken] = useState(''), [found, setFound] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const waiting = phone && !phone.paired && !phone.connected;
  useEffect(() => { api('/api/phone').then(setPhone).catch((e) => setError(e.message)); }, []);
  // Send it from the phone (encrypted, through kencomputer.dev): a QR while they're on this step.
  useEffect(() => {
    if (!waiting || phone.handoff) return;
    api('/api/phone/handoff', { json: {} }).then((h) => setPhone((p) => ({ ...p, handoff: h }))).catch(() => {});
  }, [waiting, !!phone?.handoff]);  // and again after "No, it isn't"
  useEffect(() => () => { api('/api/phone/handoff', { method: 'DELETE' }).catch(() => {}); }, []);
  // Every 2s: has the phone sent the code, or tapped Start on the new bot?
  useEffect(() => {
    if (!phone || phone.paired) return;
    const timer = setInterval(() => api('/api/phone').then((p) => {
      setPhone(p);
      if (p.paired) { toast?.(`Ken is on your phone. Say hi to @${p.bot}.`); onPaired?.(); }
    }).catch(() => {}), 2000);
    return () => clearInterval(timer);
  }, [phone?.paired]);
  // Only a bot token is ever read from the clipboard, nothing else.
  const fromClipboard = async (asked) => {
    let code = null;
    try {
      code = window.kenDesktop?.botTokenFromClipboard ? await window.kenDesktop.botTokenFromClipboard()
        : asked ? ((await navigator.clipboard.readText()).match(BOT_TOKEN) || [])[0] : null;
    } catch {}
    if (code) { setBotToken(code); setFound(true); setError(''); }
    else if (asked) setError('Nothing to paste yet. Copy the code in Telegram first.');
  };
  useEffect(() => {
    if (!waiting) return;
    const look = () => { if (!document.hidden) fromClipboard(false); };
    look(); addEventListener('focus', look);
    return () => removeEventListener('focus', look);
  }, [waiting]);
  const act = async (opts) => {
    setBusy(true); setError('');
    try { setPhone(await api(opts.path || '/api/phone', opts)); setBotToken(''); } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  const on = (label) => html`<span class="conn-on"><i></i>${label}</span>`;
  if (!phone) return html`<p class="np-note">${error || 'Loading…'}</p>`;
  if (phone.paired) return html`<div class="conn-list phone-list"><div>${on(phone.owner ? `@${phone.bot} · ${phone.owner}` : '@' + phone.bot)}<button class="text-action" disabled=${busy} onClick=${() => act({ method: 'DELETE' })}>Disconnect</button></div></div>`;
  if (phone.handoff?.bot) return html`<div class="phone-confirm">
    <p class="phone-confirm-q">Is this the bot you just made?</p>
    <p class="phone-confirm-bot">@${phone.handoff.bot}</p>
    <button class="btn primary" disabled=${busy} onClick=${() => act({ json: {}, path: '/api/phone/handoff/confirm' })}>${busy ? 'Connecting…' : 'Connect'}</button>
    <button class="text-action" disabled=${busy} onClick=${() => act({ method: 'DELETE', path: '/api/phone/handoff' })}>Not mine</button></div>`;
  if (phone.connected) return html`<div class="phone-last-step"><p>Last step: on your phone, open <a class="text-action" href=${phone.link} target="_blank" rel="noopener">@${phone.bot}</a> and say hi.</p>
    <button class="text-action" disabled=${busy} onClick=${() => act({ method: 'DELETE' })}>Use a different bot</button></div>`;
  return html`<div class="phone-steps-box">
    ${phone.handoff?.qr ? html`<div class="phone-lead">
        <div class="phone-lead-qr" dangerouslySetInnerHTML=${{ __html: phone.handoff.qr }}></div>
        <video class="phone-video" src="/media/botfather.mp4" autoplay muted loop playsinline aria-label="What you'll do in Telegram"></video>
      </div>
      <p class=${'phone-lead-note' + (phone.handoff.error ? ' err' : '')}>${phone.handoff.error || 'Scan with your phone\u2019s camera. It walks you through making your Telegram bot, about 2 minutes.'}</p>`
    : html`<div class="phone-how">
      <video class="phone-video" src="/media/botfather.mp4" autoplay muted loop playsinline aria-label="What you'll do in Telegram"></video>
      <div>
        <ol class="phone-steps">
          <li>In Telegram, open <a class="text-action" href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> and tap <b>Start</b></li>
          <li>Send <code>/newbot</code>, then a name, then a username ending in <code>bot</code></li>
          <li>Copy the long code it sends back and paste it below</li>
        </ol>
        <div class="phone-qr"><img src="/media/botfather-qr.svg" alt="QR code that opens @BotFather"/><span>Scan with your phone's camera to open @BotFather</span></div>
      </div>
    </div>`}
    <div class="token-row"><input class="np-input" placeholder=${phone.handoff?.qr ? "Or paste the code here" : "Paste the code, or BotFather's whole message"} value=${botToken}
          onInput=${(e) => { const v = e.target.value, code = (v.match(BOT_TOKEN) || [])[0]; setBotToken(code || v); setFound(!!code && code !== v.trim()); }}/>
          <button class="btn" onClick=${() => fromClipboard(true)}>Paste</button>
          <button class="btn primary" disabled=${busy || !botToken.trim()} onClick=${() => act({ json: { token: botToken } })}>${busy ? 'Checking…' : 'Connect'}</button></div>
        ${(error || found) && html`<p class=${'token-hint' + (error ? ' err' : '')} role=${error ? 'alert' : 'status'}>${error || 'Found the code from BotFather'}</p>`}
  </div>`;
}

// First run, step 2 of 2 (after Connect Claude): the two ways to reach Ken, and the phone set up
// or skipped. Then the chat opens and Ken takes it from there.
function PhoneSetup({ onDone, toast }) {
  const [busy, setBusy] = useState(false);
  const skip = async () => { setBusy(true); try { onDone(await api('/api/setup', { json: { action: 'skip-phone' } })); } catch (e) { toast(e.message); setBusy(false); } };
  return html`<div class="connect phone-setup"><img src="/ken.svg" alt=""/><h1>Ken on your phone</h1>
    <p>Text or voice-note Ken from anywhere with Telegram. Same Ken, same chat.</p>
    <${PhoneSteps} toast=${toast} onPaired=${() => api('/api/setup').then(onDone).catch(() => {})}/>
    <button class="text-action" disabled=${busy} onClick=${skip}>Skip for now (you can do it later in Settings)</button>
  </div>`;
}

// Ken's jobs, the same in Settings and the Brain: what each does in a line, when, and on or off.
// To change what a job does, tell Ken.
function JobList({ jobs, change }) {
  const name = (j) => (!j.agent && JOB_NAMES[j.name]) || j.name.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const when = (j) => j.days === 'daily' ? 'Every day' : j.days === 'weekdays' ? 'Weekdays' : j.days;
  const about = (j) => (j.agent_name ? j.agent_name + ' · ' : '') + ((!j.agent && JOB_HELP[j.name]) || j.summary || when(j));
  return html`<div class="job-list">${jobs.map((j) => html`<div class="job" key=${(j.agent || '') + ':' + j.name}>
    <div class="job-row">
      <div class="job-name"><b>${name(j)}</b><span>${about(j)}</span></div>
      <input class="job-time" type="time" aria-label="Time" value=${j.time} disabled=${!j.on} onChange=${(e) => e.target.value && change(j, { time: e.target.value })}/>
      <input class="job-switch" type="checkbox" role="switch" aria-label=${j.on ? 'On' : 'Off'} checked=${j.on} onChange=${(e) => change(j, { on: e.target.checked })}/>
    </div>
  </div>`)}</div>`;
}

function Settings({ onClose, toast }) {
  const [error, setError] = useState(''), [reconnect, setReconnect] = useState(false);
  const [jobs, setJobs] = useState([]), [tab, setTab] = useState('claude');
  const [privacy, setPrivacy] = useState(null);
  useEffect(() => { api('/api/jobs').then(setJobs).catch(() => {}); api('/api/privacy').then(setPrivacy).catch(() => {}); }, []);
  const share = async (on) => { setError(''); try { setPrivacy(await api('/api/privacy', { method: 'PUT', json: { share: on } })); } catch (e) { setError(e.message); } };
  const job = async (j, change) => { setError(''); try { setJobs(await api(`/api/jobs/${encodeURIComponent(j.name)}`, { method: 'PATCH', json: { ...change, agent: j.agent } })); return true; } catch (e) { setError(e.message); return false; } };
  const row = (icon, title, text, right, body) => html`<div class="conn"><div class="conn-row"><span class="conn-icon">${icon}</span><div class="conn-text"><b>${title}</b><span>${text}</span></div><div class="conn-right">${right}</div></div>${body}</div>`;
  const on = (label) => html`<span class="conn-on"><i></i>${label}</span>`;
  const icon = (d) => html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d=${d}/></svg>`;
  return html`<${Modal} title="Settings" onClose=${onClose}>
    <div class="set-tabs" role="tablist">${[['claude', 'Claude'], ['phone', 'Phone'], ['jobs', 'Jobs'], ['privacy', 'Privacy']].map(([id, label]) => html`<button role="tab" aria-selected=${tab === id} class=${tab === id ? 'on' : ''} onClick=${() => { setTab(id); setError(''); }}>${label}</button>`)}</div>
    ${tab === 'claude' && html`<div class="set-body"><p class="np-help">Ken runs on your Claude subscription.</p>
    ${reconnect ? html`<${Connect} onDone=${() => { setReconnect(false); toast('Claude is connected.'); }}/>`
      : html`<div class="conn-list"><div>${on('Connected to your Claude account')}<button class="text-action" onClick=${() => setReconnect(true)}>Reconnect</button></div></div>`}</div>`}
    ${tab === 'phone' && html`<div class="set-body phone-body"><p class="np-help">Text or talk to Ken from anywhere with Telegram. Same Ken, same chat.</p><${PhoneSteps} toast=${toast}/></div>`}
    ${tab === 'privacy' && privacy && html`<div class="set-body"><p class="np-help">Everything Ken knows and does stays on this computer.</p>
    ${row(icon('M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z'), 'Share anonymous crash reports and usage',
      'Helps fix bugs and shows which features get used: counts like “a job ran”. Never what you say, your files or your logins.',
      html`<input class="job-switch" type="checkbox" role="switch" aria-label=${privacy.share ? 'On' : 'Off'} checked=${privacy.share} onChange=${(e) => share(e.target.checked)}/>`)}
    <p class="np-note privacy-more"><a class="text-action" href="https://kencomputer.dev/privacy" target="_blank" rel="noopener">Exactly what's sent</a></p></div>`}
    ${tab === 'jobs' && html`<div class="set-body"><p class="np-help">What Ken does on its own. You can also just tell Ken to change these.</p>
    <${JobList} jobs=${jobs} change=${job}/></div>`}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
  <//>`;
}
const JOB_NAMES = { 'morning-brief': 'Morning brief', 'nightly-review': 'Nightly review' };
const JOB_HELP = { 'morning-brief': 'What matters today, sent to you each morning.', 'nightly-review': 'Files the day into Ken’s memory and tells you what it saved.' };

function RenameProject({ project, onClose, onDone }) {
  const [name, setName] = useState(project.name), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const save = async () => { setBusy(true); try { await api(`/api/agents/${project.id}`, { method: 'PATCH', json: { name: name.trim() } }); onDone(); } catch (e) { setError(e.message); setBusy(false); } };
  return html`<${Modal} title="Rename agent" onClose=${onClose}>
    <label class="np-label">Name<input class="np-input" autofocus value=${name} onInput=${(e) => setName(e.target.value)} onKeyDown=${(e) => e.key === 'Enter' && name.trim() && !busy && save()}/></label>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    <div class="row np-actions"><button class="btn ghost" onClick=${onClose}>Cancel</button><button class="btn primary" disabled=${busy || !name.trim()} onClick=${save}>Rename</button></div>
  <//>`;
}

// Keys: named values Ken gets as environment variables, and whole files (a service-account JSON).
// Stored where only you can read them; a value is never shown again, and Ken masks it in chat.
function Keys({ pid, initial }) {
  const [keys, setKeys] = useState(initial || null), [name, setName] = useState(''), [value, setValue] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const base = `/api/agents/${pid}/keys`;
  useEffect(() => { if (!initial) api(base).then(setKeys).catch((e) => setError(e.message)); }, []);
  const act = async (fn) => { setBusy(true); setError(''); try { setKeys(await fn()); return true; } catch (e) { setError(e.message); return false; } finally { setBusy(false); } };
  const add = async () => { if (await act(() => api(`${base}/${encodeURIComponent(name.trim())}`, { method: 'PUT', json: { value } }))) { setName(''); setValue(''); } };
  const addFile = (file) => file && act(() => api(`${base}/${encodeURIComponent(file.name)}?file=1`, { method: 'PUT', body: file }));
  const remove = (k) => act(() => api(`${base}/${encodeURIComponent(k.name)}`, { method: 'DELETE' }));
  return html`<div class="keys">
    ${keys && keys.map((k) => html`<div class="key-row"><code>${k.kind === 'file' ? k.name : '$' + k.name}</code><span>${k.kind === 'file' ? 'file' : '••••••'}</span><button class="text-action" disabled=${busy} onClick=${() => remove(k)}>Remove</button></div>`)}
    ${keys && !keys.length && html`<p class="np-note">No keys yet. When Ken needs a login or API key, it asks you to add it here.</p>`}
    <div class="key-add"><input class="np-input" aria-label="Key name" placeholder="STRIPE_KEY" value=${name} onInput=${(e) => setName(e.target.value.toUpperCase())}/>
      <input class="np-input" aria-label="Key value" type="password" autocomplete="off" placeholder="Paste the value" value=${value} onInput=${(e) => setValue(e.target.value)} onKeyDown=${(e) => e.key === 'Enter' && name.trim() && value && !busy && add()}/>
      <button class="btn primary" disabled=${busy || !name.trim() || !value} onClick=${add}>Add</button></div>
    <label class="text-action key-file">Add a file (like a service-account JSON)…<input type="file" hidden onChange=${(e) => { addFile(e.target.files[0]); e.target.value = ''; }}/></label>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
  </div>`;
}

const EXAMPLES = ['Plan my day', 'Catch me up on my email', 'Set up a morning brief'];
const TOOL_NAMES = { browser: 'Chrome', email: 'Email', calendar: 'Calendar' };

// Ken's Brain: who it is (SOUL.md), its memory notes, tools, keys, jobs and files. A calm overview
// of one line each; open a line to see and edit that part.
function About({ pid, onClose, onPreview, page, onAddTool }) {
  const [a, setA] = useState(null), [conn, setConn] = useState(null), [jobs, setJobs] = useState([]), [section, setSection] = useState(null);
  const [edit, setEdit] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const load = () => {
    api(`/api/about/${pid}`).then(setA).catch((e) => setError(e.message));
    api('/api/connections').then(setConn).catch(() => {});
    api('/api/jobs').then((all) => setJobs(all.filter((j) => (j.agent || 'home') === pid))).catch(() => {});
  };
  useEffect(load, []);
  const save = async (fn) => { setBusy(true); setError(''); try { setA(await fn()); setEdit(null); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  const openNote = (name) => api(`/api/about/${pid}/memory/${encodeURIComponent(name)}`).then((n) => setEdit({ kind: 'note', name, text: n.text })).catch((e) => setError(e.message));
  const noteName = (n) => (n.trim().replace(/[^\w .-]/g, '').replace(/\.md$/i, '') || 'note') + '.md';
  const job = async (j, change) => { try { const all = await api(`/api/jobs/${encodeURIComponent(j.name)}`, { method: 'PATCH', json: { ...change, agent: j.agent } }); setJobs(all.filter((x) => (x.agent || 'home') === pid)); return true; } catch (e) { setError(e.message); return false; } };
  const jobName = (j) => (pid === 'home' && JOB_NAMES[j.name]) || j.name.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const frame = (title, body) => page
    ? html`<div class="knows-page"><div class="knows-inner">${title && html`<h1>${title}</h1>`}${body}</div></div>`
    : html`<${Modal} wide title=${title || `About ${a ? a.name : ''}`} onClose=${edit ? () => setEdit(null) : onClose}>${body}<//>`;
  const back = (to, label) => html`<button class="text-action brain-back" onClick=${to}>‹ ${label}</button>`;
  if (edit) return frame(edit.kind === 'context' ? (pid === 'home' ? 'Who Ken is' : 'Context') : edit.isNew ? 'New note' : edit.name.replace(/\.md$/, ''), html`
    ${edit.isNew && html`<input class="np-input" placeholder="Note name, like people" value=${edit.name} onInput=${(e) => setEdit({ ...edit, name: e.target.value })}/>`}
    <textarea class="np-input about-editor" value=${edit.text} onInput=${(e) => setEdit({ ...edit, text: e.target.value })}></textarea>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    <div class="row np-actions">
      ${edit.kind === 'note' && !edit.isNew && html`<button class="btn ghost" style="margin-right:auto" disabled=${busy} onClick=${() => save(() => api(`/api/about/${pid}/memory/${encodeURIComponent(edit.name)}`, { method: 'DELETE' }))}>Delete note</button>`}
      <button class="btn ghost" onClick=${() => setEdit(null)}>Cancel</button>
      <button class="btn primary" disabled=${busy || (edit.isNew && !edit.name.trim())} onClick=${() => save(() => edit.kind === 'context'
        ? api(`/api/about/${pid}/context`, { method: 'PUT', json: { text: edit.text } })
        : api(`/api/about/${pid}/memory/${encodeURIComponent(edit.isNew ? noteName(edit.name) : edit.name)}`, { method: 'PUT', json: { text: edit.text } }))}>Save</button>
    </div>`);
  if (!a) return frame(null, html`<p class="np-help">${error || 'Loading…'}</p>`);
  const plain = (t) => t.replace(/[*_`#>]/g, '').trim();
  const firstLine = a.context.split('\n').map(plain).find((l) => l && !/^You are \w+$/.test(l)) || 'Nothing yet';
  const connected = conn ? { browser: conn.chrome, email: conn.emails.length > 0, calendar: conn.calendars.length > 0 } : {};
  const on = a.builtin.filter((t) => connected[t] && a.tools.use.includes(t)).map((t) => TOOL_NAMES[t]).concat(Object.keys(a.tools.mcp));
  const off = conn ? a.builtin.filter((t) => !connected[t]).map((t) => TOOL_NAMES[t]) : [];
  const count = (n, one) => `${n} ${one}${n === 1 ? '' : 's'}`;
  const parts = [
    { key: 'soul', title: pid === 'home' ? 'Who Ken is' : 'Context', summary: firstLine, body: html`
        <div class="brain-actions"><button class="text-action" onClick=${() => setEdit({ kind: 'context', text: a.context })}>Edit</button></div>
        <div class="about-context">${a.context.trim() ? doc(a.context) : 'Nothing yet.'}</div>` },
    { key: 'memory', title: 'Memory', summary: a.memory.length ? count(a.memory.length, 'note') : 'Nothing yet', body: html`
        <div class="brain-actions"><button class="text-action" onClick=${() => setEdit({ kind: 'note', isNew: true, name: '', text: '' })}>New note</button></div>
        ${a.memory.length ? a.memory.map((n) => html`<button class="about-item" onClick=${() => openNote(n.name)}><b>${n.name.replace(/\.md$/, '')}</b><span>${n.about}</span></button>`) : html`<p class="np-note">No notes yet. Ken writes them as it learns about you.</p>`}` },
    { key: 'tools', title: 'Tools', summary: [on.join(', ') || 'Nothing connected', off.length && off.join(', ') + ' not connected'].filter(Boolean).join(' · '), body: html`
        <${ToolList} tools=${a.tools} onUse=${(t, yes) => save(() => api(`/api/agents/${pid}/tools`, { method: 'PUT', json: { use: yes ? [...a.tools.use, t] : a.tools.use.filter((x) => x !== t) } }))} onAddTool=${onAddTool}/>` },
    { key: 'keys', title: 'Keys', summary: a.keys.length ? count(a.keys.length, 'key') : 'None yet', body: html`<${Keys} pid=${pid} initial=${a.keys}/>` },
    jobs.length > 0 && { key: 'jobs', title: 'Jobs', summary: jobs.filter((j) => j.on).map((j) => `${jobName(j)} ${j.time}`).join(' · ') || 'All off', body: html`<${JobList} jobs=${jobs} change=${job}/>` },
    a.files.length > 0 && { key: 'files', title: 'Files', summary: count(a.files.length, 'file'), body: a.files.slice(0, 30).map((f) => html`<button class="about-item" onClick=${() => onPreview({ ...f, project: pid })}><b>${f.name}</b><span>${bytes(f.size)}</span></button>`) },
  ].filter(Boolean);
  const open = parts.find((p) => p.key === section);
  return frame(null, html`
    ${open ? html`<div class="brain-section">${back(() => { setSection(null); load(); }, 'Brain')}<h2>${open.title}</h2>${open.body}</div>`
      : html`<div class="brain-rows">${parts.map((p) => html`<button class="brain-row" onClick=${() => setSection(p.key)}><b>${p.title}</b><span>${p.summary}</span><i aria-hidden="true">›</i></button>`)}</div>`}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    ${!page && html`<div class="row np-actions"><button class="btn ghost" onClick=${onClose}>Done</button></div>`}`);
}


function RemoveProject({ project, onClose, onDone }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const remove = async () => { setBusy(true); try { await api(`/api/agents/${project.id}`, { method: 'DELETE' }); onDone(); } catch (e) { setError(e.message); setBusy(false); } };
  return html`<${Modal} title=${`Remove “${project.name}” from Ken?`} onClose=${onClose}>
    <p class="np-help">It leaves the sidebar. Its folder and files on your computer aren't touched, and its chat and notes are kept in Ken's folder.</p>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    <div class="row np-actions"><button class="btn ghost" onClick=${onClose}>Cancel</button><button class="btn primary" disabled=${busy} onClick=${remove}>Remove</button></div>
  <//>`;
}

function NewAgent({ onClose, onCreated }) {
  const [name, setName] = useState(''), [job, setJob] = useState(''), [path, setPath] = useState(''), [more, setMore] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const pick = async () => { const p = await window.kenDesktop.pickFolder(); if (p) { setPath(p); if (!name) setName(p.split('/').pop()); } };
  const ready = name.trim() && job.trim() && !busy;
  const create = async () => {
    setBusy(true); setError('');
    try { onCreated(await api('/api/chats', { json: { name: name.trim(), context: job.trim(), path, setup: true } })); } catch (e) { setError(e.message); setBusy(false); }
  };
  return html`<${Modal} title="New agent" onClose=${onClose}>
    <p class="np-help">An agent does one job for you, with its own chat, memory, schedule and tools. Tell it what you want; it sets itself up.</p>
    <label class="np-label">Name<input class="np-input" autofocus value=${name} placeholder="Myreader" onInput=${(e) => setName(e.target.value)}/></label>
    <label class="np-label">What should it do?<textarea class="np-input" rows="4" value=${job} placeholder="Run Myreader for me: fix bugs, watch Stripe, send me a money report every morning." onInput=${(e) => setJob(e.target.value)} onKeyDown=${(e) => e.key === 'Enter' && (e.metaKey || e.ctrlKey) && ready && create()}></textarea></label>
    ${more || path ? html`<div class="np-label">Folder <span class="np-optional">optional</span>
      ${window.kenDesktop?.pickFolder
        ? html`<div class="np-folder"><span title=${path}>${path ? path.replace(/^\/Users\/[^/]+/, '~').split('/').slice(-2).join('/') : 'Ken makes one for it'}</span><button class="text-action" onClick=${pick}>${path ? 'Change' : 'Choose folder…'}</button></div>`
        : html`<input class="np-input" value=${path} placeholder="~/code/myreader" onInput=${(e) => setPath(e.target.value)}/>`}
      <small class="np-note">Where its code or info is. It still lives in its own folder under Ken and goes here for the job.</small></div>`
      : html`<button class="text-action" onClick=${() => setMore(true)}>Point it to a code or info folder…</button>`}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    <div class="row np-actions"><button class="btn ghost" onClick=${onClose}>Cancel</button><button class="btn primary" disabled=${!ready} onClick=${create}>${busy ? 'Creating…' : 'Create agent'}</button></div>
  <//>`;
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
  const [setup,setSetup]=useState(null);
  const [chats,setChats]=useState([]);
  const [newProject,setNewProject]=useState(false);
  const [projectDialog,setProjectDialog]=useState(null); // {kind:'rename'|'remove', project}
  const [connections,setConnections]=useState(false);
  const [projectMeta,setProjectMeta]=useState(null);
  const loadProjectMeta=(id)=>{ if(id==='home'){setProjectMeta(null);return;} api(`/api/agents/${id}`).then((v)=>setProjectMeta(v.agent)).catch(()=>setProjectMeta(null)); };
  const refreshChats=()=>api('/api/chats').then(setChats).catch(()=>{});
  // ⋯ and right-click on a project: a native menu in the Mac app.
  const projectMenu=async(e,id)=>{
    const native=window.kenDesktop; if(!native?.popupMenu)return;
    const project=chats.find((c)=>c.id===id); if(!project)return;
    const choice=await native.popupMenu([{id:'open',label:'Open in Finder'},{id:'keys',label:'About, keys and tools…'},{id:'rename',label:'Rename…'},{id:'folder',label:'Code or info folder…'},{separator:true},{id:'remove',label:'Remove from Ken…'}]);
    if(choice==='open'){ const v=await api(`/api/agents/${id}`); native.openFolder(v.agent.workdir); }
    if(choice==='keys')setProjectDialog({kind:'keys',project});
    if(choice==='rename')setProjectDialog({kind:'rename',project});
    if(choice==='remove')setProjectDialog({kind:'remove',project});
    if(choice==='folder'){ const path=await native.pickFolder(); if(!path)return;
      try{ await api(`/api/agents/${id}`,{method:'PATCH',json:{path}}); toast(`${project.name} now knows its job is in ${path.replace(/^\/Users\/[^/]+/,'~')}.`); if(id===pid)loadProjectMeta(id); }catch(err){toast(err.message);} }
  };
  // Unread: a chat whose last message is newer than when it was last open.
  const seen=()=>{try{return JSON.parse(localStorage.getItem('ken-seen')||'{}');}catch{return {};}};
  const [seenAt,setSeenAt]=useState(seen);
  // A "Check for Updates…" the user asked for (Ken menu, menu-bar icon) answers with a quiet toast.
  const [updateFailed,setUpdateFailed]=useState(null);
  useEffect(()=>{
    const native=window.kenDesktop; if(!native?.onUpdateState)return;
    native.updateFailed?.().then((s)=>{if(s)setUpdateFailed(s);}).catch(()=>{});
    return native.onUpdateState((s)=>{
      if(s.state==='failed')setUpdateFailed(s);
      if(s.state==='ready')toast(`Ken ${s.version} is ready. Restart from the Ken menu when you like.`);
      if(s.state==='none')toast(`You’re up to date (${s.version}).`);
      if(s.state==='downloading')toast(`Downloading Ken ${s.version}…`);
      if(s.state==='busy')toast('Ken is in the middle of something. It’ll restart into the update when it’s done.');
      if(s.state==='error')toast('Couldn’t check for updates. Try again later.');
    });
  },[]);
  const loadSetup=()=>api('/api/setup').then(setSetup).catch(()=>{});
  useEffect(()=>{loadSetup();},[]);
  // Poll while something is still getting ready (sign-in elsewhere, the voice model download).
  useEffect(()=>{if(!setup || (setup.claude && setup.voice.state==='ready'))return;const t=setInterval(loadSetup,2000);return()=>clearInterval(t);},[setup?.claude,setup?.voice?.state]);
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
    api('/api/chats').then(setChats).catch(() => {});
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
  useEffect(() => { load(); loadProjectMeta(pid); }, [pid]);
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
  const projects = today ? today.agents : [];
  const current = chats.find((c) => c.id === pid);
  useEffect(()=>{ if(!current)return; const next={...seen(),[pid]:Math.max(current.last_ts,Date.now()/1000)}; try{localStorage.setItem('ken-seen',JSON.stringify(next));}catch{} setSeenAt(next); },[pid,current?.last_ts]);
  // When you last read this chat, as of opening it: where the first unread reply starts.
  const opened = useRef({ pid: null, ts: 0 });
  if (opened.current.pid !== pid) opened.current = { pid, ts: seenAt[pid] || 0 };
  const visibleChat = chat && chat.pid === pid ? { ...chat, messages: [...chat.messages, ...outbox.filter((m) => m.project === pid && !chat.messages.some((saved) => saved.client_id === m.client_id))] } : null;
  return html`<div class="shell"><main class="main">${updateFailed && html`<div class="update-failed" role="alert">Ken couldn’t update itself to ${updateFailed.version}. <a href=${updateFailed.url} target="_blank" rel="noopener">Download the new version</a> and drag it into Applications over this one. Your chats and memory stay.</div>`}<${ChatHeader} title=${pid !== 'home' && current ? current : null} brain=${route.brain} onSettings=${()=>setConnections(true)} onProjectMenu=${pid !== 'home' && window.kenDesktop?.popupMenu ? (e)=>projectMenu(e,pid) : null} connected=${!setup || setup.claude} awake=${awake} onAwake=${()=>api('/api/command',{json:{text:awake?'/decaf':'/coffee'}}).then((r)=>{setAwake(r.awake);toast(r.awake?'☕ Coffee’s on — lock your screen anytime; Ken keeps working.':'Decaf — your Mac can sleep as usual.');}).catch((e)=>toast(e.message))} status=${loadError ? 'Can’t reach Ken' : offline ? 'Reconnecting…' : ''} onRetry=${loadError ? load : null} onClear=${clearThread} clearing=${clearing} model=${pid !== 'home' && model ? { ...model, current: projectMeta?.model || model.current } : model} onModel=${(m)=>m&&(pid !== 'home'
      ? api(`/api/agents/${pid}`,{method:'PATCH',json:{model:m}}).then(()=>loadProjectMeta(pid))
      : api('/api/command',{json:{text:'/model '+m}}).then(loadModel)).catch((e)=>toast(e.message))}/>
    ${setup && !setup.claude ? html`<${Connect} onDone=${(r)=>{setSetup(r);loadModel();warm(pid);}}/>` : setup && setup.phone === 'unset' ? html`<${PhoneSetup} toast=${toast} onDone=${(r)=>{setSetup(r);load();}}/>` : route.brain ? html`<${About} page pid="home" onPreview=${setPreview} onAddTool=${(name)=>{location.hash='#/';send(`Add ${name} as a tool for you. Set it up whichever way works best (an MCP server, a command-line tool or its API), and ask me for anything you need, one thing at a time. Keys go in Keys, not in this chat.`,[],undefined,'home').catch((e)=>toast(e.message));}}/>` : html`<${ChatPage} seenTs=${opened.current.ts} voiceSetup=${setup?.voice} key=${pid + ':' + (chat && chat.pid === pid ? chat.cleared_at || 0 : 0)} pid=${pid} chat=${visibleChat} data=${today} files=${library.filter((f) => f.project === pid)} stream=${stream[pid]} activity=${activity[pid]} voice=${meta.voice && (!setup || setup.voice.state === 'ready')} toast=${toast} onPreview=${setPreview} draft=${pid === 'home' ? draft : null} onDraftUsed=${() => setDraft(null)} openRun=${setRunId} projects=${projects} onSend=${send} onVoice=${sendVoice} choices=${choices} onChoice=${(text)=>{setChoices(null);command(text).catch((e)=>toast(e.message));}}/>`}
  </main>
    ${runId && html`<${RunDrawer} rid=${runId} projects=${projects} tick=${tick} onClose=${() => setRunId(null)} toast=${toast}/>`}
    ${preview && html`<${PreviewModal} file=${preview} onClose=${() => setPreview(null)} toast=${toast}/>`}
    ${connections && html`<${Settings} toast=${toast} onClose=${()=>setConnections(false)}/>`}
    ${projectDialog?.kind==='keys' && html`<${About} pid=${projectDialog.project.id} onPreview=${setPreview} onClose=${()=>setProjectDialog(null)}/>`}
    ${projectDialog?.kind==='rename' && html`<${RenameProject} project=${projectDialog.project} onClose=${()=>setProjectDialog(null)} onDone=${()=>{setProjectDialog(null);refreshChats();}}/>`}
    ${projectDialog?.kind==='remove' && html`<${RemoveProject} project=${projectDialog.project} onClose=${()=>setProjectDialog(null)} onDone=${()=>{const gone=projectDialog.project.id;setProjectDialog(null);refreshChats();if(gone===pid)location.hash='#/';}}/>`}
    ${newProject && html`<${NewAgent} onClose=${()=>setNewProject(false)} onCreated=${(p)=>{setNewProject(false);api('/api/chats').then(setChats).catch(()=>{});location.hash='#/p/'+p.id;}}/>`}
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
