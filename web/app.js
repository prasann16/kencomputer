// Ken: one conversation, with tools and settings available when needed.
import { html, render, useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo, useErrorBoundary } from '/vendor/preact-htm.js';

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
  console.error(message);
  remember(message);
  const report = openReport && Object.assign(document.createElement('button'), { textContent: 'Report it', onclick: () => openReport() });
  bar.replaceChildren(document.createTextNode('Something went wrong on this page. '), reload, ...(report ? [report] : []));
}
// The last few errors on this page go with a problem report, so a bug can be found without asking what happened.
const RECENT_ERRORS = [];
function remember(message) {
  RECENT_ERRORS.push(`${new Date().toISOString().slice(11, 19)} ${String(message).slice(0, 600)}`);
  if (RECENT_ERRORS.length > 5) RECENT_ERRORS.shift();
}
let openReport = null;  // set by App: opens Report a problem from anywhere
addEventListener('error', (e) => { reportUiError(e.error || e.message, 'page'); showFailure(e.message); });
// A mouse click (on a button, tab or link) shouldn't leave a focus ring behind (it shows up on the next key press, like ⌘\).
// Keyboard presses (detail 0) keep focus, so tabbing still shows where you are.
addEventListener('click', (e) => { if (e.detail > 0) e.target.closest?.('button, a, [role="tab"]')?.blur(); });
addEventListener('unhandledrejection', (e) => reportUiError(e.reason, 'background'));

// A part of the page that breaks hides itself instead of taking the page down: chatting keeps working. The error goes to the console and, when crash reports are on, to Ken's crash reports.
function reportUiError(error, where) {
  console.error(`Ken page (${where}):`, error);
  try {
    const message = String(error?.stack || error?.message || error).slice(0, 4000);
    remember(message);
    fetch('/api/ui-error', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ where, message }) }).catch(() => {});
  } catch {}
}
function Safe({ where, children }) {
  const [error] = useErrorBoundary((e) => reportUiError(e, where));
  return error ? null : children;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.json !== undefined || opts.body !== undefined ? 'POST' : 'GET'), headers: {} };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers['Content-Type'] = 'application/json';
  } else if (opts.body !== undefined) {
    init.body = opts.body;
  }
  // Nothing waits forever: a request Ken doesn't answer fails with plain words, and whatever asked shows a way out.
  init.signal = AbortSignal.timeout(opts.timeout || 30000);
  let r;
  try { r = await fetch(path, init); }
  catch (e) {
    throw new Error(e.name === 'TimeoutError' || e.name === 'AbortError'
      ? 'Ken didn’t answer in time. Check your connection and try again.'
      : 'Can’t reach Ken right now. Check your connection and try again.');
  }
  if (r.status === 401) { location.reload(); throw new Error('signed out'); }
  const data = await r.json().catch(() => ({}));
  // A code from the host (e.g. "container_unreachable" while Ken's computer wakes) is never shown as is.
  const said = typeof data.error === 'string' && !/^[a-z0-9_.-]+$/.test(data.error) ? data.error : '';
  if (!r.ok) throw new Error(said || (r.status >= 500 ? 'Ken is waking up. Try again in a moment.' : r.statusText));
  return data;
}

// Files the user attached; they show in their message, not as Ken's file cards.
const ATTACHED = new Set();
// Big files (a phone's raw video) go in 32 MB pieces: a proxy in front of a Ken in the cloud refuses any one request
// over 100 MB. A piece that fails is sent again (three tries), and Ken says where to carry on, so nothing doubles.
const PIECE = 32 * 1024 * 1024;
async function uploadPieces(pid, file, query, onProgress) {
  const { id } = await api(`/api/chats/${pid}/uploads`, { json: { name: file.name, size: file.size } });
  let offset = 0;
  while (offset < file.size) {
    let tries = 0;
    for (;;) {
      try {
        const r = await api(`/api/chats/${pid}/uploads/${id}?offset=${offset}`, { method: 'PUT', body: file.slice(offset, offset + PIECE), timeout: 300000 });
        offset = r.received;
        break;
      } catch (e) {
        if (++tries >= 3) throw new Error(`Couldn’t finish uploading ${file.name}. ${e.message}`);
        await new Promise((ok) => setTimeout(ok, 2000 * tries));
      }
    }
    onProgress?.(offset / file.size);
  }
  return (await api(`/api/chats/${pid}/uploads/${id}/done${query}`, { json: {}, timeout: 120000 })).saved || [];
}

// fetch can't report how much it has sent, so a file upload goes through XMLHttpRequest to show a live percentage.
function sendWithProgress(path, body, onProgress) {
  return new Promise((ok, fail) => {
    const x = new XMLHttpRequest();
    x.open('POST', path);
    x.timeout = 600000;
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    x.onload = () => {
      if (x.status === 401) { location.reload(); return fail(new Error('signed out')); }
      let data = {};
      try { data = JSON.parse(x.responseText); } catch {}
      const said = typeof data.error === 'string' && !/^[a-z0-9_.-]+$/.test(data.error) ? data.error : '';
      if (x.status < 200 || x.status >= 300) return fail(new Error(said || (x.status >= 500 ? 'Ken is waking up. Try again in a moment.' : x.statusText)));
      ok(data);
    };
    x.onerror = () => fail(new Error('Can’t reach Ken right now. Check your connection and try again.'));
    x.ontimeout = () => fail(new Error('Ken didn’t answer in time. Check your connection and try again.'));
    x.send(body);
  });
}

async function upload(pid, list, attach, onProgress) {
  const query = attach ? '?attach=1' : '';
  let saved;
  if (typeof list[0] === 'string') saved = (await api(`/api/chats/${pid}/files/paths${query}`, { json: { paths: list }, timeout: 600000 })).saved || [];  // picked in the Mac app
  else {
    saved = [];
    const small = list.filter((f) => f.size <= 48 * 1024 * 1024), big = list.filter((f) => f.size > 48 * 1024 * 1024);
    // One percentage across every file picked together.
    const total = list.reduce((n, f) => n + f.size, 0) || 1;
    let done = 0;
    // Small files go up together, in batches under 90 MB: the proxy refuses any one request over 100 MB.
    const batches = [];
    for (const f of small) {
      const last = batches[batches.length - 1];
      if (last && last.size + f.size <= 90 * 1024 * 1024) { last.files.push(f); last.size += f.size; }
      else batches.push({ files: [f], size: f.size });
    }
    if (batches.length) onProgress?.(0);
    for (const b of batches) {
      const fd = new FormData();
      for (const f of b.files) fd.append('file', f, f.name);
      saved.push(...((await sendWithProgress(`/api/chats/${pid}/files${query}`, fd, (part) => onProgress?.((done + part * b.size) / total))).saved || []));
      done += b.size;
    }
    for (const f of big) {
      saved.push(...await uploadPieces(pid, f, query, (part) => onProgress?.((done + part * f.size) / total)));
      done += f.size;
    }
  }
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
// Files Ken names open on click: [name](/path), `work/report.md`, or a full path in the text.
const LINK_RE = /\[([^\]\n]+)\]\(<?([^()\s<>]+|[^()<>\n]+(?=>))>?\)/g;
const PATH_RE = /((?:~|\/)[^\s<>()`'"*]*\/[^\s<>()`'"*]*\.[A-Za-z0-9]{1,5})(?![\w/])/g;
const isPath = (s) => /^(~\/|\/|[\w.-]+\/)\S*(\.[A-Za-z0-9]{1,5}|\/)$/.test(s) && !/^\/\//.test(s);
let notify = () => {}, showDoc = () => {};
// Ken on a computer in the cloud, used in the browser (from /api/meta): no Mac features, and its own browser if the plan has one.
let HOSTED = false, HOSTED_BROWSER = false;
// Ken in the cloud: its computer sleeps after 5 quiet minutes, and awake time counts against the plan. A tab nobody
// has touched for this long (no typing, clicking, scrolling) stops talking to it, even while it's on screen.
const IDLE_MS = 5 * 60000;
let lastTouch = Date.now(), napping = false;
const idleTab = () => HOSTED && (document.hidden || napping);
const sharedUrl = (path) => `/api/file?path=${encodeURIComponent(path)}`;
async function openFile(path) {
  try {
    const found = await api('/api/open', { json: { path } });
    if (/\.(md|markdown)$/i.test(found.path)) {  // a write-up reads best right here, formatted
      const r = await fetch(sharedUrl(found.path));
      if (r.ok) return showDoc({ ...found, text: await r.text() });
    }
    if (window.kenDesktop?.openFile) await window.kenDesktop.openFile(found.path);
    else if (!found.folder) window.open(sharedUrl(found.path), '_blank', 'noopener');
    else notify('That’s a folder on the computer Ken runs on: ' + found.path);
  } catch (e) { notify(e.message); }
}
const fileLink = (path, label) => html`<a class="file-link" href="#" title=${path} onClick=${(e) => { e.preventDefault(); openFile(path); }}>${label}</a>`;
function words(text) {
  return text.split(URL_RE).map((part, j) => {
    if (j % 2) return html`<a href=${part} target="_blank" rel="noopener">${part}</a>`;
    return part.split(PATH_RE).map((p, k) => k % 2 ? fileLink(p, p.split('/').pop())
      : p.split(/\*\*([^*\n]+)\*\*/g).map((b, n) => (n % 2 ? html`<b>${b}</b>`
        : b.split(/(?<![*\w])\*([^*\s][^*\n]*?)\*(?![*\w])/g).map((t, q) => (q % 2 ? html`<i>${t}</i>` : t)))));
  });
}
function inline(text) {
  return text.split(/(`[^`\n]+`)/g).map((seg, i) => {
    if (i % 2) { const code = seg.slice(1, -1); return isPath(code) ? fileLink(code, code.replace(/\/$/, '').split('/').pop()) : html`<code>${code}</code>`; }
    const parts = seg.split(LINK_RE);
    return parts.map((part, j) => {
      if (j % 3 === 1) return null;
      if (j % 3 === 2) { const label = parts[j - 1];
        return /^https?:\/\//.test(part) ? html`<a href=${part} target="_blank" rel="noopener">${label}</a>` : isPath(part) || part.startsWith('/') ? fileLink(part, label) : label; }
      return words(part);
    });
  });
}
const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
// Quoted lines (> ...) show as a quote, without the marks: Ken quotes drafts that way, ready to copy and paste.
function prose(text) {
  const parts = text.split(/((?:^[ \t]*>.*(?:\n|$))+)/m);  // the quote is its own block: no line break either side
  return parts.map((part, i) => i % 2
    ? html`<blockquote>${prose(part.replace(/^[ \t]*> ?/gm, '').replace(/\n$/, ''))}</blockquote>`
    : tables(parts.length > 1 ? part.replace(i ? /^\n/ : /$^/, '').replace(i < parts.length - 1 ? /\n$/ : /$^/, '') : part));
}
function tables(text) {
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

// A notes file as a page: headings, bullet and numbered lists and paragraphs, with the same inline markdown.
function doc(text) {
  const blocks = [];
  let list = null, numbered = 0, para = [];
  const flush = () => {
    if (para.length) blocks.push(html`<p>${inline(para.join(' '))}</p>`);
    if (list) blocks.push(numbered ? html`<ol start=${numbered}>${list.map((li) => html`<li>${inline(li)}</li>`)}</ol>` : html`<ul>${list.map((li) => html`<li>${inline(li)}</li>`)}</ul>`);
    para = []; list = null;
  };
  for (const line of String(text || '').split('\n')) {
    const h = line.match(/^(#{1,4})\s+(.*)/), li = line.match(/^\s*(?:[-*]|(\d+)[.)])\s+(.*)/);
    if (h) { flush(); blocks.push(h[1].length === 1 ? html`<h2>${inline(h[2])}</h2>` : html`<h3>${inline(h[2])}</h3>`); }
    else if (li) { if (para.length || (list && !numbered !== !li[1])) { const l = list && !numbered === !li[1] ? list : null; flush(); list = l; } if (!list) numbered = +(li[1] || 0); (list = list || []).push(li[2]); }
    else if (!line.trim()) flush();
    else { if (list) flush(); para.push(line.trim()); }
  }
  flush();
  return blocks;
}

// A whole Markdown file: headings, lists and paragraphs, plus tables and code blocks.
function page(text) {
  return String(text || '').split(/```[^\n]*\n?([\s\S]*?)```/g).map((block, i) => i % 2
    ? html`<pre><code>${block.replace(/\n$/, '')}</code></pre>`
    : block.split(/((?:^\|.*\|[ \t]*(?:\n|$)){2,})/m).map((part, j) => {
      const lines = part.trim().split('\n');
      if (j % 2 === 0 || !/^\|[\s:|-]+\|$/.test(lines[1] || '')) return doc(part);
      return html`<div class="table"><table><thead><tr>${cells(lines[0]).map((c) => html`<th>${inline(c)}</th>`)}</tr></thead>
        <tbody>${lines.slice(2).map((l) => html`<tr>${cells(l).map((c) => html`<td>${inline(c)}</td>`)}</tr>`)}</tbody></table></div>`;
    }));
}

// A write-up fills the window like a page to read: back to the chat, its name once, Copy and Finder.
function DocViewer({ file, onClose }) {
  useEffect(() => { const esc = (e) => e.key === 'Escape' && onClose(); addEventListener('keydown', esc); return () => removeEventListener('keydown', esc); }, []);
  const copy = () => navigator.clipboard.writeText(file.text).then(() => notify('Copied.'), () => notify('Couldn’t copy.'));
  const finder = () => window.kenDesktop?.showFile ? window.kenDesktop.showFile(file.path) : window.open(sharedUrl(file.path), '_blank', 'noopener');
  return html`<div class="doc-viewer" role="dialog" aria-label=${file.name}>
    <header>
      <button class="btn ghost sm doc-back" onClick=${onClose}><span class="flip">${Glyph('arrow')}</span>Chat</button>
      <b class="ellipsis" title=${file.path}>${file.name}</b>
      <button class="btn ghost sm" onClick=${copy}>${Icon.copy}Copy</button>
      <button class="btn ghost sm" onClick=${finder}>${window.kenDesktop?.showFile ? 'Show in Finder' : 'Download'}</button>
    </header>
    <div class="doc-scroll"><div class="doc-page">${file.text.trim() ? page(file.text) : 'This file is empty.'}</div></div>
  </div>`;
}

// -------------------------------------------------------------------- icons

const Icon = {
  talk: html`<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"/></svg>`,
  copy: html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15H4.5A1.5 1.5 0 0 1 3 13.5v-9A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5V5"/></svg>`,
  check: html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>`,
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
const COMMANDS = [['/new', 'Fresh conversation · memory stays'], ['/stop', 'Stop Ken’s current reply'], ['/model', 'See or switch the AI model'], ['/usage', 'How much of your AI plan and memory is used'], ['/coffee', 'Keep this Mac awake'], ['/decaf', 'Let this Mac sleep normally']];
const MAC_ONLY = ['/coffee', '/decaf'];

function waveformPeaks(values, count = 48) {
  // Keep each interval's peak; point-sampling can miss whole words in a note.
  return Array.from({length:count}, (_, i) => {
    const start = Math.floor(i * values.length / count), end = Math.max(start + 1, Math.floor((i + 1) * values.length / count));
    let peak = 0;
    for (let n = start; n < end; n++) peak = Math.max(peak, values[n] || 0);
    return peak;
  });
}

// Bars of a fixed thin width spread across whatever width the wave has (the live one while recording fills the
// composer), so it never stretches into blocks. The live wave fits one bar every 5px of the room it has.
const LIVE_BAR_PITCH = 5;
function Waveform({ stream, peaks = [], onPeak, progress = 0 }) {
  const svg = useRef(null), [liveBars,setLiveBars] = useState(48);
  useLayoutEffect(() => {
    if (!stream || !svg.current) return;
    const fit = () => setLiveBars(Math.max(12, Math.floor((svg.current?.clientWidth || 240) / LIVE_BAR_PITCH)));
    fit(); window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [stream]);
  useEffect(() => {
    if (!stream) return;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    const context = new Audio(), source = context.createMediaStreamSource(stream), analyser = context.createAnalyser();
    analyser.fftSize = 1024; source.connect(analyser); context.resume().catch(() => {});
    const data = new Float32Array(analyser.fftSize), history = Array(liveBars).fill(0);
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
  }, [stream, liveBars]);
  const bars = stream ? Array(liveBars).fill(0) : waveformPeaks(peaks), peak = Math.max(.001, ...bars);
  return html`<svg ref=${svg} class=${'voice-wave' + (stream ? ' live-wave' : '')} height="32" role="img" aria-label=${stream ? 'Live microphone waveform' : 'Voice recording'}>${bars.map((p,i) => {
    const height = Math.max(2, Math.pow(p / peak, .65) * 26);
    return html`<rect x=${`${(i / bars.length) * 100}%`} y=${(32-height)/2} width="2.5" height=${height} rx="1.25" class=${i / bars.length < progress ? 'played' : ''}/>`;
  })}</svg>`;
}

function VoiceNote({ src, peaks = [], seconds, pending, failed, onRetry, failure, transcript }) {
  const audio = useRef(null), [playing,setPlaying] = useState(false), [position,setPosition] = useState(0), [length,setLength] = useState(seconds || 0);
  // Upload completion and message acknowledgement must not restart playback.
  const source = useRef(src);
  // A recording that can't be played (a Ken in the cloud may not keep it) stays a picture: waveform, length, words.
  const [unplayable,setUnplayable] = useState(false);
  useEffect(()=>{const player=audio.current;return()=>{player?.pause();if(source.current.startsWith('blob:'))URL.revokeObjectURL(source.current);};},[]);
  const play = async () => { try { if (playing) audio.current.pause(); else { if(audio.current.ended)audio.current.currentTime=0; await audio.current.play(); } } catch { setUnplayable(true); } };
  const progress = length ? Math.min(1, position / length) : 0;
  const seek = (e) => { const next = Number(e.target.value); try { audio.current.currentTime=next;setPosition(next); } catch {} };
  return html`<div class="voice-note"><div class="voice-note-row">${!unplayable && html`<button class="voice-play" aria-label=${playing ? 'Pause voice note' : 'Play voice note'} onClick=${play}>${playing ? Icon.pause : Icon.play}</button>`}
    <div class="voice-seek">${peaks.length ? html`<${Waveform} peaks=${peaks} progress=${progress}/>` : html`<div class="voice-track"><span style=${{width:progress*100+'%'}}></span></div>`}${!unplayable && html`<input type="range" min="0" max=${length || 0} step="0.1" value=${position} disabled=${!length} aria-label="Seek voice note" aria-valuetext=${durationLabel(position)+' of '+durationLabel(length)} onInput=${seek}/>`}</div>
    <span class="voice-time">${durationLabel(playing || position > 0 ? position : length)}</span></div>
    <audio ref=${audio} src=${source.current} preload="metadata" onPlay=${()=>{document.querySelectorAll('.voice-note audio').forEach(other=>{if(other!==audio.current && !other.paused)other.pause();});setPlaying(true);}} onPause=${()=>setPlaying(false)} onEnded=${()=>{setPlaying(false);setPosition(0);}} onTimeUpdate=${()=>setPosition(audio.current.currentTime)} onLoadedMetadata=${()=>{if(Number.isFinite(audio.current.duration))setLength(audio.current.duration);}} onError=${()=>setUnplayable(true)}></audio>
    ${transcript ? html`<div class="voice-transcript">${transcript}</div>` : pending && html`<div class="voice-state" role="status"><span class="voice-pending" aria-hidden="true"></span>Listening…</div>`}
    ${failed && html`<div class="voice-error">${failure || 'Couldn’t process this voice note.'} ${/No microphone audio/i.test(failure||'') ? window.kenDesktop?.microphoneSettings && html`<button class="text-action" onClick=${()=>window.kenDesktop.microphoneSettings('input')}>Microphone settings</button>` : html`<button class="text-action" onClick=${onRetry}>Retry voice note</button>`}</div>`}</div>`;
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

function Composer({ placeholder, onSend, onVoice, onAttach, voice, busy, onStop, toast, draft, dropped, onDraftUsed, storageKey, onTalk }) {
  const savedDraft = (() => { try { return JSON.parse(sessionStorage.getItem('ken-draft:' + storageKey) || '{}'); } catch { return {}; } })();
  const [text, setText] = useState(savedDraft.text || '');
  const [files, setFiles] = useState(savedDraft.files || []);
  (savedDraft.files || []).forEach((name) => ATTACHED.add(name));
  useEffect(() => { sessionStorage.setItem('ken-draft:' + storageKey, JSON.stringify({ text, files })); }, [text, files, storageKey]);
  useEffect(() => { if (draft) { setText(draft.text); onDraftUsed(); requestAnimationFrame(() => ta.current?.focus()); } }, [draft]);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [uploading, setUploading] = useState(0);
  const [uploaded, setUploaded] = useState(null);  // a big file's progress, 0 to 1
  const ta = useRef(null);
  const picker = useRef(null);
  const id = useRef('c' + Math.random().toString(36).slice(2, 8)).current;
  const recorder = useRecorder((blob,peaks,seconds)=>onVoice(blob,peaks,seconds),toast);
  const recording = recorder.status === 'recording', voiceBusy = recorder.status !== 'idle';
  const commands = text.startsWith('/') && !text.includes(' ') ? COMMANDS.filter(([name])=>name.startsWith(text) && !(HOSTED && MAC_ONLY.includes(name))) : [];
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
      const saved = await onAttach([...list], setUploaded);
      setFiles((f) => [...new Set([...f, ...saved])]);
    } catch (e) { toast(e.message); }
    finally { setUploading((n) => n - 1); setUploaded(null); }
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
    ${uploading > 0 && html`<div class="upload-status" role="status">${uploaded === null ? 'Attaching files…' : uploaded >= 1 ? 'Saving on Ken…' : `Uploading… ${Math.round(uploaded * 100)}%`}${uploaded !== null && html`<div class="upload-bar"><span style=${`width:${Math.round(uploaded * 100)}%`}></span></div>`}</div>`}
    ${files.length > 0 && html`<div class="attached">${files.map((f) => html`<span class="filechip">${isImage(f)?html`<img src=${`/api/chats/${storageKey}/files/${encodeURIComponent(f)}`} alt=${f}/>`:Icon.file}${f}
      <button class="x" aria-label=${'Remove ' + f} onClick=${() => setFiles(files.filter((x) => x !== f))}>${Icon.close}</button></span>`)}</div>`}
    <div class="composer" onClick=${(e) => { if (e.target === e.currentTarget) ta.current?.focus(); }}>
      ${voiceBusy ? html`<div class="recording-input"><button class="icon-btn" aria-label="Cancel recording" onClick=${()=>recorder.finish(false)}>${Icon.close}</button>${recorder.status==='requesting' ? html`<span class="recording-wait" role="status">Allow microphone…</span>` : html`<${Waveform} stream=${recorder.stream} onPeak=${recorder.peak}/>`}${recorder.status!=='requesting' && html`<span class="voice-time">${recording && html`<i class="rec-dot" aria-label="Recording"></i>`}${durationLabel(recorder.seconds)}</span>`}</div>` : html`<label class="sr" for=${id}>${placeholder}</label>
      <textarea id=${id} ref=${ta} rows="1" placeholder=${placeholder} value=${text}
        onInput=${(e) => setText(e.target.value)} onKeyDown=${onKey}></textarea>`}
      ${onAttach && !voiceBusy && html`<button class="icon-btn attach-button" aria-label="Attach files" title="Attach files" onClick=${() => window.kenDesktop?.pickFiles ? window.kenDesktop.pickFiles().then(attach).catch((e) => toast(e.message)) : picker.current.click()}>${Icon.plus}</button>
        <input ref=${picker} type="file" multiple hidden onChange=${(e) => { attach(e.target.files); e.target.value = ''; }} />`}
      ${voice && !voiceBusy && html`<button class="icon-btn" aria-label="Voice" title="Record a voice note" onClick=${recorder.start}>${Icon.mic}</button>`}
      ${onTalk && !voiceBusy && html`<button class="icon-btn talk-button" aria-label="Talk with Ken" title="Talk with Ken (voice mode)" onClick=${onTalk}>${Icon.talk}</button>`}
      ${busy && !ready && !voiceBusy && onStop
        ? html`<button class="icon-btn hot" aria-label="Stop reply" title="Stop this reply" onClick=${() => Promise.resolve(onStop()).catch((e) => toast(e.message))}>${Icon.stop}</button>`
        : (ready || voiceBusy) && html`<button class="icon-btn hot" aria-label="Send" disabled=${recording ? !recorder.heard : (!ready || sending || voiceBusy || uploading > 0)} onClick=${send}>${Icon.send}</button>`}
    </div>
  </div>`;
}

// Copy a message as it reads on screen: plain text, without the markdown marks.
async function copyText(text) {
  if (window.kenDesktop?.copyText) return window.kenDesktop.copyText(text);
  try { await navigator.clipboard.writeText(text); }
  catch { const area = document.createElement('textarea'); area.value = text; document.body.append(area); area.select(); document.execCommand('copy'); area.remove(); }
}
function CopyButton({ from }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { const el = from.current; if (!el) return; await copyText(el.innerText.trim()); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  return html`<button class="copy-msg" title=${copied ? 'Copied' : 'Copy'} aria-label=${copied ? 'Copied' : 'Copy message'} onClick=${copy}>${copied ? Icon.check : Icon.copy}</button>`;
}

function Message({ m, label }) {
  const body = useRef(null);
  const files = m.files || [];
  const content = useMemo(() => md(m.text), [m.text]);
  const media = useMemo(() => (m.role === 'you' ? [] : mediaIn(m.text)), [m.text, m.role]);
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
      ${m.text && !recording && html`<div class="bubble" ref=${body}>${content}</div>`}
      ${m.via === 'telegram' && html`<div class="msg-from">📱 from Telegram</div>`}
      ${m.via === 'voice' && !m.call && m.role === 'you' && html`<div class="msg-from">🎙️ said on a voice call</div>`}
    </div></div>`;
  }
  // A voice call, after it ended: what was said, folded away.
  if (m.call) {
    const mins = Math.max(1, Math.round((m.call.seconds || 0) / 60));
    return html`<div class="msg talk-log"><details><summary>🎙️ Voice call · ${mins} min</summary>
      <div class="talk-lines">${m.text.split('\n').map((line) => { const [who, ...rest] = line.split(': '); return html`<p><b>${who}</b> ${rest.join(': ')}</p>`; })}</div></details></div>`;
  }
  // A scheduled job's report says what it is, so Ken never seems to talk out of nowhere.
  return html`<div class=${'msg' + (m.error ? ' err' : '') + (m.job ? ' from-job' : '')}>
    ${label && html`<div class="job-label">${m.job.label}${m.job.time && html`<span> · ${m.job.time}</span>`}<a class="text-action" href="#/brain" title="Turn it off or change its time">Change</a></div>`}
    <div class="msg-body"><div class="txt">
    ${m.run_title && html`<div class="from-run">${m.run_status === 'done' ? '✓' : '✕'} ${m.run_title}</div>`}
    <div ref=${body}>${content}</div>${media.map((p) => html`<${Media} key=${p} path=${p}/>`)}</div>${m.text && html`<${CopyButton} from=${body}/>`}</div></div>`;
}

// A video or picture Ken names is shown right in its message, ready to watch.
const MEDIA_RE = /\.(mp4|mov|webm|m4v|png|jpe?g|gif|webp)$/i;
function mediaIn(text) {
  const found = [], names = new Set();
  const add = (p) => { const name = p.split('/').pop(); if (MEDIA_RE.test(p) && isPath(p) && !names.has(name)) { names.add(name); found.push(p); } };
  for (const [, code] of String(text || '').matchAll(/`([^`\n]+)`/g)) add(code);
  for (const [, , target] of String(text || '').matchAll(LINK_RE)) add(target.trim());
  for (const [p] of String(text || '').replace(/`[^`\n]+`/g, '').matchAll(PATH_RE)) add(p);
  return found.slice(0, 3);
}
function Media({ path }) {
  const [gone, setGone] = useState(false);
  if (gone) return null;
  const url = sharedUrl(path), name = path.split('/').pop();
  // Saving it is one tap: the browser's own video menu has no Save in Safari, and a picture opens instead of downloading.
  return html`<div class="msg-media">${/\.(mp4|mov|webm|m4v)$/i.test(name)
    ? html`<video src=${url} controls preload="metadata" playsinline onError=${() => setGone(true)}></video>`
    : html`<img src=${url} alt=${name} loading="lazy" onClick=${() => openFile(path)} onError=${() => setGone(true)}/>`}
    <a class="media-save" href=${url + '&download'} download=${name}>${Glyph('download')} Download</a></div>`;
}

// Text arrives in bursts; reveal it at a steady pace that catches up with each burst.
function useSmooth(text) {
  const [shown, setShown] = useState(0), target = useRef(text);
  target.current = text;
  useEffect(() => { let frame; const tick = () => { setShown((n) => { const t = target.current.length; return n > t ? 0 : n + Math.ceil((t - n) / 6); }); frame = requestAnimationFrame(tick); }; frame = requestAnimationFrame(tick); return () => cancelAnimationFrame(frame); }, []);
  return text.slice(0, shown);
}

function Live({ stream, activity, onStop }) {
  const text = useSmooth(stream || '');
  // Never a spinner without end: Ken stops an AI that doesn't answer within two minutes and says so; if this
  // screen hears nothing for longer than that, it says so too and offers Stop.
  const [waited, setWaited] = useState(0);
  useEffect(() => { setWaited(0); const started = Date.now(); const t = setInterval(() => setWaited(Date.now() - started), 5000); return () => clearInterval(t); }, [stream, activity]);
  const label = activity || (stream ? 'Writing…' : waited > 150000 ? 'This is taking longer than it should' : waited > 30000 ? 'Still thinking…' : 'Thinking…');
  // The status line sticks to the bottom of the view, so it stays visible under a long answer.
  const status = html`<div class="typing" role="status"><span class="pulse"></span><span class="ellipsis">${label}</span>${!stream && !activity && waited > 150000 && onStop && html` <button class="text-action" onClick=${onStop}>Stop</button>`}</div>`;
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
      await api(`/api/approvals/${a.id}`, { json: { decision, body: editing ? body : undefined }, timeout: 120000 });
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
  const step = run.status === 'queued' ? 'Waiting for a free slot' : run.activity || 'Working';
  return html`<div class="card run">
    <span class="run-dot" aria-hidden="true"></span>
    <div class="run-text">
      <div class="title ellipsis">${run.title}</div>
      <div class="run-step ellipsis" key=${step}>${showProject && project ? project.name + ' · ' : ''}${step} · ${elapsed(run)}</div>
    </div>
    <div class="run-actions">
      <button class="text-action" onClick=${() => openRun(run.id)}>Watch</button>
      <button class="text-action" onClick=${() => stopRun(run.id, toast)}>Stop</button>
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
  const [run, setRun] = useState(null), [failed, setFailed] = useState('');
  const logRef = useRef(null);
  useEffect(() => { api(`/api/runs/${rid}`).then((r) => { setRun(r); setFailed(''); }).catch((e) => { setFailed(e.message); toast(e.message); }); }, [rid, tick]);
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
          <h2>${run ? run.title : failed ? 'Couldn’t load this job' : 'Loading…'}</h2>
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

function Modal({ title, children, onClose, wide, className, closeX }) {
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
    <div ref=${box} tabindex="-1" class=${'modal' + (wide ? ' wide' : '') + (className ? ' ' + className : '')} role="dialog" aria-modal="true" aria-label=${title}>${closeX && html`<button class="modal-x" aria-label="Close" onClick=${onClose}><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>`}<h2>${title}</h2>${children}</div>
  </div>`;
}

// ---------------------------------------------------------------- desktop UI
function Glyph(name) {
  const paths = {
    home: 'M3 10 12 3 21 10M5 9v11h5v-6h4v6h5V9',
    mail: 'M4 6h16v12H4zM4 7l8 6 8-6',
    chat: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z',
    folder: 'M3 7V5h7l2 3h9v12H3V7Z',
    browser: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3c5 6 5 12 0 18-5-6-5-12 0-18Z',
    settings: 'm9 3-1 3-3 1-2 5 2 5 3 1 1 3h6l1-3 3-1 2-5-2-5-3-1-1-3H9ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
    arrow: 'M5 12h14m-6-6 6 6-6 6',
    file: 'M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h6',
    download: 'M12 4v11m-5-5 5 5 5-5M5 20h14',
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

// Ken is switched off (Settings → Turn Ken off): nothing runs until they turn it back on, here.
function KenOff({ toast }) {
  const [busy, setBusy] = useState(false);
  const on = async () => { setBusy(true); try { await api('/api/power', { json: { on: true } }); } catch (e) { toast(e.message); setBusy(false); } };
  return html`<section class="conversation-body"><div class="starting ken-off"><img src="/ken.svg" alt=""/>
    <h2>Ken is off</h2><p class="quiet-copy">Nothing runs: no replies, briefs or jobs. Your plan, memory and files are kept.</p>
    <button class="btn primary" disabled=${busy} onClick=${on}>${busy ? 'Turning on…' : 'Turn Ken on'}</button></div></section>`;
}

// Ken AI credits out: one clear way on, and one quiet link for people who already pay for Claude or ChatGPT.
// Free trial: a plan (they come with credits). Paid plan: add credits (a bigger plan lives in Plan & billing).
function CreditChoices({ onConnect, usage }) {
  const paid = !!usage?.topup_url;
  return html`<div class="credit-choices">
    ${!paid && html`<p class="credit-why">Plans start at $5 a month and include AI credits.</p>`}
    <div class="msg-actions">${paid ? html`<a class="btn primary sm" href=${usage.topup_url}>Add credits</a>` : html`<a class="btn primary sm" href="/account/plans">Pick a plan</a>`}</div>
    <button class="text-action credit-own" onClick=${() => onConnect('claude')}>Already pay for Claude or ChatGPT? Use that instead.</button>
  </div>`;
}

function ChatPage({ pid, seenTs, chat, data, files, stream, activity, voice, toast, onPreview, draft, onDraftUsed, openRun, projects, onSend, onVoice, choices, onChoice, voiceSetup, loadError, onReload, onAIAccounts, onConnect, onTalk, usage }) {
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
  // A reply that failed (the AI was out, refused or didn't answer): try the same message again, or use another AI.
  const last = messages[messages.length - 1], failed = last && last.role === 'ken' && last.error && last.action;
  const [retrying, setRetrying] = useState(false);
  useEffect(() => setRetrying(false), [last?.id]);
  const retry = () => { setRetrying(true); api(`/api/chats/${pid}/retry`, { json: {} }).catch((e) => { setRetrying(false); toast(e.message); }); };
  const stop = () => api(`/api/chats/${pid}/stop`, { json: {} }).catch((e) => toast(e.message));
  const clearedAt = chat ? chat.cleared_at || 0 : 0;
  const busy = chat && chat.busy;
  const byId = Object.fromEntries(projects.map((p) => [p.id, p]));
  const approvals = data ? data.approvals.filter((a) => a.project === pid) : [];
  const runs = data ? data.running.filter((r) => r.project === pid) : [];
  const timeline = [
    ...messages.map((m, i) => ({ t: m.ts, key: 'm' + (m.client_id || m.id), mine: m.role === 'you', el: html`<${Message} m=${m} label=${m.job && messages[i - 1]?.job?.at !== m.job.at}/>${m.note && m.action === 'credits' && html`<${CreditChoices} onConnect=${onConnect} usage=${usage}/>`}${m.delivery === 'failed' && !m.audio && html`<div class="delivery" role="status">Couldn’t send. <button class="text-action" onClick=${() => onSend(m.text, m.files, m.client_id).catch((e) => toast(e.message))}>Retry</button></div>`}` })),
    ...approvals.map((a) => ({ t: a.created, key: 'a' + a.id, el: html`<${ApprovalCard} a=${a} project=${byId[a.project]} toast=${toast}/>` })),
    ...runs.map((r) => ({ t: r.created, key: 'r' + r.id, el: html`<${RunCard} run=${r} project=${byId[r.project]} openRun=${openRun} toast=${toast} showProject=${r.project !== 'home'}/>` })),
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
      onScroll=${() => { if (thread.current && atEnd(thread.current)) setBelow(false); }}>
      <div class="thread conversation-thread">
        ${!chat && (loadError ? html`<p class="quiet-copy" role="alert">Couldn’t load your conversation. ${loadError} <button class="text-action" onClick=${onReload}>Try again</button></p>`
          : html`<p class="quiet-copy" role="status">Loading your conversation…</p>`)}
        ${chat && !messages.length && !busy && html`<div class="chat-empty"><p>What can Ken take off your plate?</p>
          <div class="chat-examples">${EXAMPLES.map((t) => html`<button class="btn sm" onClick=${() => { sent(); onSend(t, []).catch((e) => toast(e.message)); }}>${t}</button>`)}</div></div>`}
        ${timeline.map((item) => html`<div key=${item.key} data-key=${item.key}><${Safe} where="message">${item.el}<//></div>`)}
        ${!busy && failed && (last.action === 'credits' ? html`<${CreditChoices} onConnect=${onConnect} usage=${usage}/>` : html`<div class="msg-actions">${last.action === 'retry'
          ? html`<button class="btn sm" disabled=${retrying} onClick=${retry}>${retrying ? 'Trying again…' : 'Try again'}</button>`
          : html`<button class="btn sm" onClick=${onAIAccounts}>${last.action === 'connect' ? 'Connect your Claude or ChatGPT' : `Reconnect ${last.brain === 'codex' ? 'ChatGPT' : 'Claude'}`}</button>`}</div>`)}
        ${busy && (stream || activity || messages[messages.length - 1]?.role !== 'ken') ? html`<${Safe} where="live reply"><${Live} stream=${stream} activity=${activity} onStop=${stop}/><//>`
          : chat && messages.length > 0 && html`<div class="typing idle" aria-hidden="true"><span class="pulse"></span><span class="ellipsis">\u00a0</span></div>`}
      </div>
    </div>
    <div class="chat-bottom">
      ${below && html`<button class="new-below" onClick=${toEnd}>↓ New reply</button>`}
      ${voiceSetup?.state === 'downloading' && html`<div class="voice-setup" role="status">Getting voice ready${voiceSetup.progress != null ? ` · ${Math.round(voiceSetup.progress * 100)}%` : '…'}</div>`}
      ${choices && html`<div class="model-choices">${choices.map(c=>html`<button class="btn sm" onClick=${()=>onChoice(c.text)}>${c.label}</button>`)}</div>`}
      <${Safe} where="message box"><${Composer} placeholder="Tell Ken what you need…" draft=${draft} onDraftUsed=${onDraftUsed} storageKey=${pid} dropped=${dropped} voice=${voice} toast=${toast} busy=${busy}
        onVoice=${(...args)=>{sent();onVoice(...args);}} onAttach=${(list, onProgress) => upload(pid, list, true, onProgress)} onStop=${stop}
        onSend=${async (text, files) => { sent(); await onSend(text, files); }} onTalk=${onTalk}/><//>
    </div>
    ${dragging && html`<div class="dropzone">Drop files into your message</div>`}
  </section>`;
}

// ------------------------------------------------------------------ voice mode (hosted)

// Talk with Ken out loud, back and forth. The site Ken is hosted on runs a fast voice service that does the talking:
// /voice/start gives each call a one-use link and what Ken knows about them (GET /api/voice/context), and the talker
// hands real work to Ken here (ask_ken → /api/voice/ask, remember → /api/voice/remember). The voice service's key
// stays on the site; its client and audio worklets are served by Ken (vendor/elevenlabs/README.md).
let voiceClient = null;
function loadVoiceClient() {
  voiceClient ||= new Promise((resolve, reject) => {
    const tag = Object.assign(document.createElement('script'), { src: '/vendor/elevenlabs/client.js' });
    tag.onload = () => (window.ElevenLabsClient ? resolve(window.ElevenLabsClient) : reject(new Error('Voice mode didn’t load. Try again.')));
    tag.onerror = () => { voiceClient = null; tag.remove(); reject(new Error('Voice mode didn’t load. Check your connection and try again.')); };
    document.head.append(tag);
  });
  return voiceClient;
}
const VOICE_FILES = {
  workletPaths: { rawAudioProcessor: '/vendor/elevenlabs/rawAudioProcessor.js', audioConcatProcessor: '/vendor/elevenlabs/audioConcatProcessor.js' },
  libsampleratePath: '/vendor/elevenlabs/libsamplerate.worklet.js',
};
const TALK_CONNECT_WAIT = 25000;  // the whole way to a live call: the mic, Ken's context, the voice service
const TALK_LINES = { mic: 'Allow the microphone…', connecting: 'Connecting…', listening: 'Listening', speaking: 'Ken is talking', working: 'Ken is on it…', ended: 'Call ended', failed: '' };

async function voiceStatus() {
  try {
    const r = await fetch('/voice/status', { signal: AbortSignal.timeout(10000) });
    return r.ok && (r.headers.get('content-type') || '').includes('json') ? await r.json() : null;
  } catch { return null; }
}

function VoiceMode({ onClose }) {
  const [state, setState] = useState('mic');  // mic | connecting | live | ended | failed
  const [mode, setMode] = useState('listening');
  const [working, setWorking] = useState(0);
  const [muted, setMuted] = useState(false);
  const [problem, setProblem] = useState({ text: '', retry: true });
  const [seconds, setSeconds] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const session = useRef(null), call = useRef(null), lines = useRef([]), started = useRef(0), face = useRef(null), endButton = useRef(null);

  // Once per call: tell the site it ended (it counts the minutes from the voice service's own record), and put what
  // was said into the chat.
  const wrapUp = () => {
    const c = call.current;
    if (!c || c.done) return;
    c.done = true;
    const secs = started.current ? (Date.now() - started.current) / 1000 : 0;
    api('/voice/end', { json: { conversation_id: c.conversation_id } }).catch(() => {});
    if (lines.current.length) api('/api/voice/done', { json: { lines: lines.current, seconds: secs } }).catch(() => {});
  };
  const hangUp = () => { const s = session.current; session.current = null; s?.endSession?.().catch?.(() => {}); wrapUp(); };

  useEffect(() => {
    let gone = false, timer = 0;
    const fail = (text, retry = true) => {
      if (gone) return;
      clearTimeout(timer); hangUp();
      setProblem({ text, retry }); setState('failed');
    };
    started.current = 0; call.current = null; lines.current = [];
    setState('mic'); setProblem({ text: '', retry: true }); setSeconds(0); setMuted(false); setMode('listening');
    (async () => {
      // The microphone first, asked only now that they tapped: a refusal gets a plain answer.
      try {
        const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
        mic.getTracks().forEach((t) => t.stop());
      } catch (e) {
        return fail(e && e.name === 'NotAllowedError'
          ? 'Ken can’t hear you: this site isn’t allowed to use your microphone. Allow it in your browser’s settings for this site, then try again.'
          : 'No microphone was found. Check that one is connected, then try again.');
      }
      if (gone) return;
      setState('connecting');
      timer = setTimeout(() => fail('Couldn’t connect. Check your connection and try again.'), TALK_CONNECT_WAIT);
      let Client, start;
      try {
        [Client, start] = await Promise.all([loadVoiceClient(), api('/voice/start', { json: {}, timeout: TALK_CONNECT_WAIT })]);
      } catch (e) {
        return fail(e.message, !/minutes of voice mode/.test(e.message));
      }
      if (gone) return;
      call.current = start;
      try {
        const s = await Client.Conversation.startSession({
          signedUrl: start.signed_url, connectionType: 'websocket', dynamicVariables: start.dynamic_variables, ...VOICE_FILES,
          clientTools: {
            ask_ken: async ({ request }) => {
              setWorking((n) => n + 1);
              try { return (await api('/api/voice/ask', { json: { request }, timeout: 28000 })).answer; }
              catch { return 'Ken couldn’t be reached just now. Say so, and that it’s worth trying again in a moment.'; }
              finally { setWorking((n) => n - 1); }
            },
            remember: ({ fact }) => api('/api/voice/remember', { json: { fact } }).then(() => 'Saved.', () => 'That couldn’t be saved just now.'),
          },
          onConnect: () => { clearTimeout(timer); started.current = Date.now(); if (!gone) setState('live'); },
          onModeChange: ({ mode }) => !gone && setMode(mode),
          onMessage: ({ message, role, source }) => { if (message) lines.current.push({ role: (role || source) === 'user' ? 'you' : 'ken', text: message }); },
          onDisconnect: (d) => {
            if (gone || !session.current) return;
            if (d && d.reason === 'error') return fail('The call dropped. Try again.');
            session.current = null; wrapUp(); setState('ended');
          },
          onError: (message) => console.error('voice mode:', message),
        });
        if (gone) { s.endSession(); return; }
        session.current = s;
      } catch {
        fail('Couldn’t connect. Check your connection and try again.');
      }
    })();
    return () => { gone = true; clearTimeout(timer); hangUp(); };
  }, [attempt]);

  // The call's clock, and its end: the month's minutes running out ends it here (a call is 15 minutes at most; the
  // voice service ends it there itself).
  useEffect(() => {
    if (state !== 'live') return;
    const tick = setInterval(() => {
      lastTouch = Date.now();  // talking to Ken is using it, hands-free or not
      const secs = (Date.now() - started.current) / 1000;
      setSeconds(secs);
      const max = call.current?.max_seconds || 900;
      if (secs >= max + (max >= 900 ? 5 : 0)) {
        hangUp();
        if (max < 900) { setProblem({ text: `That’s all of this month’s voice minutes. They start again on ${call.current.resets_on}. You can still type to Ken.`, retry: false }); setState('failed'); }
        else setState('ended');
      }
    }, 500);
    return () => clearInterval(tick);
  }, [state]);

  // Ken's face moves with the voices: bigger as Ken speaks, a soft ring as they do.
  useEffect(() => {
    if (state !== 'live') return;
    let frame = 0;
    const draw = () => {
      const s = session.current;
      if (s && face.current) {
        face.current.style.setProperty('--out', Math.min(1, (s.getOutputVolume?.() || 0) * 2).toFixed(3));
        face.current.style.setProperty('--in', Math.min(1, (s.getInputVolume?.() || 0) * 3).toFixed(3));
      }
      frame = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(frame);
  }, [state]);

  useEffect(() => {
    endButton.current?.focus();
    const key = (e) => { if (e.key === 'Escape') { hangUp(); onClose(); } };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, []);

  const mute = () => { const next = !muted; session.current?.setMicMuted(next); setMuted(next); };
  const line = state === 'live' ? (working ? TALK_LINES.working : muted ? 'Muted' : TALK_LINES[mode] || '') : TALK_LINES[state];
  const over = state === 'failed' || state === 'ended';
  return html`<div class=${'talk ' + state + ' ' + (state === 'live' ? mode : '') + (working ? ' working' : '')} role="dialog" aria-modal="true" aria-label="Voice call with Ken">
    <div class="talk-face" ref=${face}><img src="/ken.svg" alt=""/></div>
    <p class="talk-line" role="status">${line}</p>
    ${state === 'live' && html`<p class="talk-time">${durationLabel(seconds)}</p>`}
    ${problem.text && html`<p class="talk-problem" role="alert">${problem.text}</p>`}
    ${state === 'ended' && html`<p class="talk-note">What you said is in the chat.</p>`}
    <div class="talk-controls">
      ${state === 'live' && html`<button class=${'talk-mute' + (muted ? ' on' : '')} aria-pressed=${muted} onClick=${mute}>${muted ? 'Unmute' : 'Mute'}</button>`}
      ${state === 'failed' && problem.retry && html`<button class="talk-retry" onClick=${() => setAttempt((n) => n + 1)}>Try again</button>`}
      <button class="talk-end" ref=${endButton} onClick=${() => { hangUp(); onClose(); }}>${over ? 'Close' : 'End'}</button>
    </div>
  </div>`;
}

// claude-sonnet-4-5-20250929 → Sonnet 4.5
const modelName = (id) => { if (!id.startsWith('claude-')) return id; const [family, ...version] = id.replace(/^claude-/, '').replace(/-\d{8}$/, '').split('-'); return family[0].toUpperCase() + family.slice(1) + (version.length ? ' ' + version.join('.') : ''); };

function ModelPicker({ model, catalog, disabled, onChange }) {
  // On Ken AI credits Ken picks the model itself: no menu. Their own Claude or ChatGPT gets the full menu.
  if (model.credits) return null;
  return html`<select class="model-select" aria-label="Brain and model" title="Choose a brain and model for this chat" disabled=${disabled} value=${JSON.stringify([model.backend, model.current || ''])} onChange=${(e) => { const [backend, model] = JSON.parse(e.target.value); onChange({backend, model}); }}>
    ${model.providers.map((backend) => {
      const label = backend === 'codex' ? 'Codex' : 'Claude';
      const active = backend === model.backend;
      const choices = [...new Set([...(active ? model.choices : catalog[backend] || []), ...(active && model.current ? [model.current] : [])])];
      return html`<optgroup label=${label}><option value=${JSON.stringify([backend, ''])}>${label} · Default</option>${choices.map((id) => html`<option value=${JSON.stringify([backend, id])}>${modelName(id)}</option>`)}</optgroup>`;
    })}
  </select>`;
}

// Ken on the phone, from the chat header: a quiet phone that opens Settings → Telegram (Phone on the Mac); filled once
// it's linked. linked: true / false, or null while unknown (then it isn't shown).
function PhoneButton({ linked, onOpen }) {
  const label = linked ? 'Phone connected' : 'Connect your phone';
  return html`<button class=${'phone-link' + (linked ? ' on' : '')} aria-label=${label} title=${linked ? `${label}. Talk to Ken there too.` : 'Talk to Ken from your phone'} onClick=${onOpen}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect class="phone-body" x="6.5" y="2.75" width="11" height="18.5" rx="2.6"/><path class="phone-ear" d="M10.5 5.75h3"/><circle class="phone-dot" cx="12" cy="18" r=".6"/></svg></button>`;
}

function ChatHeader({ onClear, clearing, model, catalog, onRuntime, changingBrain, status, onRetry, awake, onAwake, connected, title, brain, onSettings, onReport, onProjectMenu, jobs = [], onJobs, phone, onPhone }) {
  return html`<header class="chat-header"><img class="ken-mark" src="/ken.svg" alt="Ken"/><nav class="top-tabs" aria-label="Ken">${title
      ? html`<span class="tab sel"><i class="proj-dot" style=${{background:title.color}}></i>${title.name}${onProjectMenu && html`<button class="proj-more" aria-label="Agent options" title="Agent options" onClick=${(e)=>onProjectMenu(e)}><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg></button>`}</span>`
      : html`<a class=${'tab' + (!brain ? ' sel' : '')} href="#/">Chat</a><a class=${'tab' + (brain ? ' sel' : '')} href="#/brain">Brain</a>`}</nav>${status && html`<span class="ken-status" role="status"><i></i>${status}${onRetry && html` · <button class="text-action" onClick=${onRetry}>Try again</button>`}</span>`}${!brain && (connected || model) && html`<div class="header-actions">${!title && phone !== null && phone !== undefined && html`<${PhoneButton} linked=${phone} onOpen=${onPhone}/>`}${connected && awake !== null && !HOSTED && html`<button class=${'coffee' + (awake ? ' on' : '')} aria-pressed=${awake} aria-label="Keep this computer awake" data-tip=${awake ? 'Coffee’s on — this computer stays awake so Ken can keep working while you’re away. The screen still turns off as usual and you can lock it (⌃⌘Q); closing the lid puts it to sleep. Click for decaf.' : 'Give this computer a coffee — it stays awake so Ken can keep working while you’re away, even with the screen off or locked. Off by default.'} onClick=${onAwake}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path class="steam" d="M8.5 7.5c-.8-1 .8-1.7 0-2.8M12 7.5c-.8-1 .8-1.7 0-2.8"/><path d="M4.5 10h12v4a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5Z"/><path d="M16.5 11.5h1.2a2.3 2.3 0 0 1 0 4.6h-1.5"/></svg></button>`}${model && html`<${ModelPicker} model=${model} catalog=${catalog} disabled=${changingBrain} onChange=${onRuntime}/>`}${connected && html`<button class="clear-button" disabled=${clearing} title="Start a fresh conversation; keep memory and files" onClick=${onClear}>Clear</button>`}</div>`}${onReport && html`<button class="report-link" aria-label="Report a problem" data-tip="Report a problem: a screenshot or a few seconds of your screen helps" onClick=${onReport}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg></button>`}<button class="settings-link" onClick=${onSettings}>Settings</button></header>`;
}

// Connect Claude or ChatGPT with their own subscription: the same screen in the Mac app and in the browser.
function Connect(props) {
  return html`<${LocalConnect} ...${props}/>`;
}

function LocalConnect({ onDone, backend = 'claude', start = false, tab = null, onBack }) {
  const isCodex = backend === 'codex', label = isCodex ? 'ChatGPT' : 'Claude';
  const [step, setStep] = useState('start'), [url, setUrl] = useState(''), [device, setDevice] = useState(''), [value, setValue] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  // In the Mac app the browser is on this computer, so the sign-in finishes by itself and a code is rarely needed.
  const [codeOpen, setCodeOpen] = useState(!window.kenDesktop);
  // In a browser (Ken in the cloud) the sign-in page opens in a tab made straight from the click, the only way a
  // browser allows; the Mac app's own browser opens by itself.
  const [pct, setPct] = useState(null);
  useEffect(() => {  // the first connect may download Claude Code or Codex
    if (!busy) { setPct(null); return; }
    const t = setInterval(() => api('/api/setup').then((r) => setPct(r.runtimes?.[backend]?.progress ?? null)).catch(() => {}), 1000);
    return () => clearInterval(t);
  }, [busy]);
  const go = async (body, tab = null) => {
    setBusy(true); setError('');
    try { const r = await api('/api/setup', { json: { ...body, backend }, timeout: 600000 }); if (r.url) { setUrl((window.kenDesktop && r.browser_url) || r.url); setDevice(r.code || ''); setValue(''); setStep('code'); if (tab) tab.location.href = r.url; else if (isCodex) window.open(r.url, '_blank', 'noopener'); } else tab?.close(); const account = r.providers?.[backend] || r; if (r.error || account.error) setError(r.error || account.error); if (account.connected && account.how !== 'included') onDone(r); }
    catch (e) { tab?.close(); setError(e.message); } finally { setBusy(false); }
  };
  const begin = () => go({ action: 'start' }, window.kenDesktop ? null : window.open('', '_blank'));
  useEffect(() => { if (start) go({ action: 'start' }, tab); }, []);
  // Approving in the browser usually connects Ken by itself, with no code to paste.
  useEffect(() => {
    if (step !== 'code') return;
    const timer = setInterval(async () => { try { const r = await api('/api/setup'), account = r.providers?.[backend] || r; if (r.error || account.error) setError(r.error || account.error); if (account.connected && account.how !== 'included') { clearInterval(timer); onDone(r); } } catch {} }, 2000);
    // A sign-in left open stops being watched after 15 minutes, and says so.
    const limit = setTimeout(() => { clearInterval(timer); setError('This sign-in timed out. Start again when you’re ready.'); }, 900000);
    return () => { clearInterval(timer); clearTimeout(limit); };
  }, [step]);
  const field = (placeholder) => html`<input class="connect-input" autofocus placeholder=${placeholder} value=${value} onInput=${(e) => setValue(e.target.value)} onKeyDown=${(e) => e.key === 'Enter' && value.trim() && !busy && go({ code: value })}/>`;
  return html`<div class="connect"><img src="/ken.svg" alt=""/><h1>Connect ${label}</h1>
    ${step === 'start' && html`<p>Sign in with your ${label} account. Ken uses your plan, so there’s nothing else to pay.</p>
      <button class="btn primary" disabled=${busy} onClick=${begin}>${busy ? `Getting ${label} ready…${pct ? ` ${Math.round(pct * 100)}%` : ''}` : `Connect ${label}`}</button>`}
    ${step === 'code' && html`<p>Finish signing in in your browser. Ken connects by itself.</p>
      ${device && html`<p>Type this code on that page:</p><p class="connect-device-code">${device}</p>`}
      <a class="text-action" href=${url} target="_blank" rel="noopener">Page didn’t open? Open the sign-in page</a>
      ${error ? html`<button class="btn primary" disabled=${busy} onClick=${begin}>Try again</button>`
        : !isCodex && (codeOpen ? html`<p class="connect-code-note">If Claude shows you a code instead, paste it here.</p>${field('Paste the code')}
      <button class="btn primary" disabled=${busy || !value.trim()} onClick=${() => go({ code: value })}>${busy ? 'Connecting…' : 'Connect'}</button>`
        : html`<button class="text-action" onClick=${() => setCodeOpen(true)}>Claude gave you a code?</button>`)}
      ${!onBack && !error && html`<button class="text-action" disabled=${busy} onClick=${begin}>Start over</button>`}`}
    ${onBack && html`<button class="text-action" disabled=${busy} onClick=${onBack}>Back</button>`}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
  </div>`;
}

// Ken on a computer in the cloud: there's no browser on that computer, so every way in works from wherever you are.
// Claude: sign in and paste the code Claude shows, paste a token from `claude setup-token`, or an API key.
// ChatGPT: open a page, type the code shown here; or an API key.

// First run, nothing connected yet: Ken runs on a Claude or a ChatGPT account. The other can be added later in Settings.
function FirstConnect({ onPick, onDone }) {
  const [backend, setBackend] = useState(''), tab = useRef(null);
  // In a browser, the sign-in tab has to be opened by the click itself; Connect points it at Claude or OpenAI.
  const pick = (b) => { tab.current = window.kenDesktop ? null : window.open('', '_blank'); setBackend(b); onPick(b); };
  if (backend) return html`<${Connect} key=${backend} backend=${backend} start tab=${tab.current} onDone=${onDone} onBack=${() => setBackend('')}/>`;
  return html`<div class="connect first-connect"><img src="/ken.svg" alt=""/><h1>Connect Ken</h1>
    <p>Ken runs on your Claude or ChatGPT subscription. Sign in once and you’re set.</p>
    <button class="btn primary" onClick=${() => pick('claude')}>Connect Claude</button>
    <button class="btn primary" onClick=${() => pick('codex')}>Connect ChatGPT</button>
    <p class="first-connect-note">You can add the other one later in Settings.</p></div>`;
}

// A brand-new Ken in the cloud, before its first words: how should it think? Their own Claude or ChatGPT (the same
// Connect as above), or Ken AI credits (free to try, or the plan's). Asked once (setup.json "ai"); Settings → AI later.
function FirstThink({ usage, onPick, onConnected, onCredits }) {
  const [backend, setBackend] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), tab = useRef(null);
  const pick = (b) => { tab.current = window.open('', '_blank'); setBackend(b); onPick(b); };
  const credits = async () => {
    setBusy(true); setError('');
    try { onCredits(await api('/api/setup', { json: { action: 'ai', ai: 'credits' } })); } catch (e) { setError(e.message); setBusy(false); }
  };
  if (backend) return html`<${Connect} key=${backend} backend=${backend} start tab=${tab.current} onBack=${() => setBackend('')}
    onDone=${(r) => api('/api/setup', { json: { action: 'ai', ai: backend } }).catch(() => r).then(onConnected)}/>`;
  const free = !usage || usage.free;
  const option = (label, sub, onClick, primary) => html`<button class=${'think-option' + (primary ? ' primary' : '')} disabled=${busy} onClick=${onClick}><b>${label}</b><span>${sub}</span></button>`;
  return html`<div class="connect first-connect first-think"><img src="/ken.svg" alt=""/><h1>How should Ken think?</h1>
    <div class="think-options" role="group" aria-label="How Ken thinks">
      ${option('Connect Claude', 'Use your Claude subscription', () => pick('claude'))}
      ${option('Connect ChatGPT', 'Use your ChatGPT subscription', () => pick('codex'))}
      ${(() => {
        // None left (a used-up trial or month): don't offer them; a paid plan can add more instead.
        const none = usage?.credits_left && !(parseFloat(usage.credits_left.replace(/[^0-9.]/g, '')) > 0);
        if (none) return usage.topup_url ? html`<a class="think-option" href=${usage.topup_url}><b>Add Ken AI credits</b><span>None left this month. Add any amount, or connect your own above.</span></a>` : '';
        return option(busy ? 'Starting…' : free ? 'Try it free' : 'Use Ken AI credits',
          free ? `${(usage?.credits_left || '$1').replace(/\.00$/, '')} of Ken AI credits, no account needed` : `${usage.credits_left ? usage.credits_left + ' of' : 'The'} Ken AI credits with your plan`, credits, true);
      })()}
    </div>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    <p class="first-connect-note">You can switch any time in Settings → AI.</p></div>`;
}


// What Ken can use, connected right here: Chrome, email, calendar, plus any tool Ken adds itself.
// A connected tool has a switch (does Ken use it); one that isn't connected has Connect.
// Connect any inbox over IMAP with its password. Ken finds who runs the email they typed (from the address, or a
// business domain's mail records) and shows the steps to make the password it signs in with. onDone gets the new
// connections (/api/connections).
function EmailForm({ onDone, onCancel }) {
  const [address, setAddress] = useState(''), [password, setPassword] = useState(''), [server, setServer] = useState('');
  const [provider, setProvider] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => {
    setProvider(null);
    if (!/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(address.trim())) return;
    let alive = true;
    const t = setTimeout(() => api('/api/connections/email-provider', { json: { address: address.trim() } }).then((r) => alive && setProvider(r)).catch(() => alive && setProvider({})), 400);
    return () => { alive = false; clearTimeout(t); };
  }, [address]);
  const ready = provider && !provider.blocked && address && password && !busy;
  const connect = async () => {
    setBusy(true); setError('');
    try { const r = await api('/api/connections/email', { json: { address, password, imap_host: server.trim() } }); setPassword(''); onDone(r); }
    catch (e) { setError(e.message); if (/server/.test(e.message)) setServer(server || ' '); }
    finally { setBusy(false); }
  };
  return html`<div class="conn-form">
    <input class="np-input" autofocus type="email" autocomplete="email" placeholder="Email address" value=${address} onInput=${(e) => setAddress(e.target.value)}/>
    ${!provider ? html`<p class="conn-steps">Type your email first. Ken then shows you which password to use and how to get it.</p>`
    : provider.blocked ? html`<p class="conn-steps">${provider.blocked}</p>` : html`
      ${provider.steps ? html`<div class="conn-walk"><p class="conn-steps">${provider.app_password ? `${provider.name} needs an app password for Ken, not your normal one:` : `${provider.name}:`}</p>
        <ol class="conn-steps">${provider.steps.map((step) => html`<li>${step}</li>`)}</ol>
        ${provider.link && html`<a class="btn sm" href=${provider.link} target="_blank" rel="noopener">${provider.link_label} ↗</a>`}</div>`
      : html`<p class="conn-steps">Use your ${provider.name ? provider.name + ' ' : ''}email password, the one you sign in to your email with.</p>`}
      <input class="np-input" type="password" autocomplete="off" placeholder=${provider.app_password ? 'Paste the app password' : 'Password'} value=${password} onInput=${(e) => setPassword(e.target.value)} onKeyDown=${(e) => e.key === 'Enter' && ready && connect()}/>
      ${server && html`<input class="np-input" placeholder="Mail server, e.g. imap.gmail.com" value=${server.trim()} onInput=${(e) => setServer(e.target.value)}/>`}`}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    <div class="row np-actions"><button class="btn ghost" onClick=${onCancel}>Cancel</button>${provider && !provider.blocked && html`<button class="btn primary" disabled=${!ready} onClick=${connect}>${busy ? 'Checking…' : 'Connect'}</button>`}</div>
  </div>`;
}

function ToolList({ tools, onUse, onAddTool, switches = true }) {
  const [state, setState] = useState(null), [open, setOpen] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [url, setUrl] = useState(''), [want, setWant] = useState('');
  useEffect(() => { api('/api/connections').then(setState).catch((e) => setError(e.message)); }, []);
  // Connecting or disconnecting Chrome happens in Chrome, so watch until it actually changes.
  const waitingChrome = state && ((open === 'chrome' && !state.chrome) || (open === 'chrome-off' && state.chrome));
  useEffect(() => {
    if (!waitingChrome) return;
    const t = setInterval(() => api('/api/connections').then(setState).catch(() => {}), 1500);
    return () => clearInterval(t);
  }, [waitingChrome]);
  const act = async (path, opts, after) => {
    setBusy(true); setError('');
    try { setState(await api(path, opts)); setOpen(''); after?.(); } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  if (!state) return html`<p class="np-note">${error || 'Loading…'}</p>`;
  const row = (icon, title, text, right, body) => html`<div class="conn"><div class="conn-row"><span class="conn-icon">${icon}</span><div class="conn-text"><b>${title}</b><span>${text}</span></div><div class="conn-right">${right}</div></div>${body}</div>`;
  const on = (label) => html`<span class="conn-on"><i></i>${label}</span>`;
  const icon = (d) => html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d=${d}/></svg>`;
  const use = (t, connected, connect) => connected
    ? switches && html`<input class="job-switch" type="checkbox" role="switch" aria-label=${`Ken uses ${TOOL_NAMES[t]}`} title="Ken uses it" checked=${tools.use.includes(t)} onChange=${(e) => onUse(t, e.target.checked)}/>`
    : connect;
  const emailRow = row(icon('M4 6h16v12H4zM4 7l8 6 8-6'), 'Email', 'Search, read and draft replies. Sends only when you say so.',
      use('email', state.emails.length > 0, open !== 'email' && html`<button class="text-action" onClick=${() => { setOpen('email'); setError(''); }}>Connect</button>`),
      html`${state.emails.length > 0 && html`<div class="conn-list">${state.emails.map((e) => html`<div>${on(e.address)}<button class="text-action" disabled=${busy} onClick=${() => act(`/api/connections/email?address=${encodeURIComponent(e.address)}`, { method: 'DELETE' })}>Disconnect</button></div>`)}${open !== 'email' && html`<div><button class="text-action" onClick=${() => { setOpen('email'); setError(''); }}>Add another</button></div>`}</div>`}
      ${open === 'email' && html`<${EmailForm} onDone=${(r) => { setState(r); setOpen(''); }} onCancel=${() => setOpen('')}/>`}`);
  const globe = icon('M21 12a9 9 0 11-18 0 9 9 0 0118 0zM3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18');
  return html`<div class="tool-list">
    ${HOSTED ? row(globe, 'Browser', HOSTED_BROWSER ? 'Ken’s own browser on its computer: opens websites, reads them and fills in forms.' : 'Ken’s own browser isn’t part of your plan.',
      HOSTED_BROWSER ? use('browser', true, null) || on('On') : null)
    : row(globe, 'Chrome', 'Browse websites, fill forms and post, using your signed-in Chrome.',
      use('browser', state.chrome, open !== 'chrome' && html`<button class="text-action" onClick=${() => { window.kenDesktop?.connectChrome?.(); setOpen('chrome'); }}>Connect</button>`),
      state.chrome ? html`<div class="conn-list"><div>${on('Connected')}<button class="text-action" onClick=${() => { window.kenDesktop?.connectChrome?.(); setOpen('chrome-off'); }}>Disconnect</button></div></div>
        ${open === 'chrome' && html`<p class="conn-steps">The first time Ken uses Chrome, Chrome asks: click <b>Allow</b>.</p>`}
        ${open === 'chrome-off' && html`<p class="conn-steps">${window.kenDesktop ? 'In the Chrome tab that opened' : html`In Chrome, at <b>chrome://inspect/#remote-debugging</b>`}, switch off <b>Allow remote debugging</b>. <button class="text-action" onClick=${() => setOpen('')}>Cancel</button></p>`}`
      : open === 'chrome' && html`<div class="conn-form"><ol class="conn-steps">
            <li>${window.kenDesktop ? 'Ken opens' : 'In Chrome, open'} <b>chrome://inspect/#remote-debugging</b>${window.kenDesktop ? ' in Chrome. There' : '. There'}, switch on <b>Allow remote debugging</b>.${window.kenDesktop ? ' No tab? Paste that address into Chrome, it’s copied.' : ''}</li>
            <li>Ken connects by itself. Waiting for Chrome…</li></ol>
            <p class="conn-steps">Needs Chrome 144 or newer.</p>
            <div class="row np-actions"><button class="btn ghost" onClick=${() => setOpen('')}>Cancel</button>${window.kenDesktop && html`<button class="btn ghost" onClick=${() => window.kenDesktop.connectChrome()}>Open Chrome page again</button>`}</div></div>`)}
    ${emailRow}
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
  const [noHandoff, setNoHandoff] = useState(false);  // the QR couldn't be made: show the steps instead
  useEffect(() => { api('/api/phone').then(setPhone).catch((e) => setError(e.message)); }, []);
  // Send it from the phone (encrypted, through kencomputer.dev): a QR while they're on this step.
  useEffect(() => {
    if (!waiting || phone.handoff) return;
    api('/api/phone/handoff', { json: {} }).then((h) => setPhone((p) => ({ ...p, handoff: h }))).catch(() => setNoHandoff(true));
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
    ${!phone.handoff && !noHandoff ? html`<div class="phone-lead phone-lead-wait" aria-busy="true"></div><p class="phone-lead-note">Scan with your phone\u2019s camera. It walks you through making your Telegram bot, about 2 minutes.</p>`
    : phone.handoff?.qr ? html`<div class="phone-lead">
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
// Ken in the cloud: Telegram is linked on the account page (the site keeps the bot); here it's just where to go.
// Hosted Ken: apps are connected on the host's Apps page; this shows which are, and links there.
// Hosted Ken's Settings → Apps: connected apps, search, and Connect, right here. The site serves Ken, so its
// /account/*.json answers on this same address. Connect goes to the app's own sign-in, which comes back here.
function HostedApps() {
  const [data, setData] = useState(null), [error, setError] = useState(''), [q, setQ] = useState(''), [busy, setBusy] = useState('');
  // Each connected app's accounts, named by Ken's computer (it asks the apps themselves: /api/apps); the site's own
  // list (no names, its own ids) stands in when Ken can't be asked.
  const [mine, setMine] = useState(null), [asking, setAsking] = useState(''), [problem, setProblem] = useState('');
  // IMAP: any inbox with its password, for technical users; Ken's computer holds these, not the app catalog.
  const [mail, setMail] = useState(null), [imap, setImap] = useState(false);
  const imapShown = !q.trim() || /imap|mail|inbox|smtp/i.test(q);
  const say = new URLSearchParams(location.search);
  const load = (query = q) => { setError(''); fetch('/account/apps.json' + (query ? '?q=' + encodeURIComponent(query) : ''), { credentials: 'same-origin' })
    .then((r) => r.ok ? r.json() : Promise.reject()).then(setData).catch(() => setError("Couldn't load your apps just now.")); };
  const loadMine = () => api('/api/apps', { timeout: 60000 }).then((r) => setMine(r.connected)).catch(() => setMine(false));
  useEffect(() => { load(''); loadMine(); api('/api/connections').then((r) => setMail(r.emails)).catch(() => setMail([])); }, []);
  useEffect(() => { const t = setTimeout(() => load(q), 300); return () => clearTimeout(t); }, [q]);
  const connect = async (slug) => {
    setBusy(slug); setError('');
    try {
      const r = await fetch('/account/apps/connect.json', { method: 'POST', credentials: 'same-origin', body: new URLSearchParams({ app: slug }) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || !body.url) throw new Error(body.error || "Couldn't start connecting just now. Try again.");
      location.href = body.url;  // the app's own sign-in; it comes back to Settings → Apps
    } catch (e) { setError(e.message); setBusy(''); }
  };
  const fromSite = (data?.connected || []).map((a) => ({ slug: a.slug, name: a.name, accounts: (a.account_list || []).slice().reverse()
    .map((x, i) => ({ id: x.id, label: x.label || `Account ${i + 1}${x.connected_at ? ' · connected ' + new Date(x.connected_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''}`, via: 'host' })) }));
  const connected = [...(mine ? mine.filter((a) => a.accounts?.length) : mine === false ? fromSite : []),
    ...(mail?.length ? [{ slug: 'imap', name: 'IMAP', accounts: mail.map((m) => ({ id: m.address, label: m.address, via: 'imap' })) }] : [])];
  const logo = (a, size) => a.slug === 'imap' ? html`<i class="imap-logo">${Glyph('mail')}</i>` : html`<img src=${'/app-logos/' + a.slug} alt="" width=${size} height=${size} loading="lazy"/>`;
  const add = (slug) => slug === 'imap' ? setImap(true) : connect(slug);
  const disconnect = async (app, account) => {
    setBusy(`off:${account.id}`); setProblem('');
    try {
      if (account.via === 'imap') {}
      else if (account.via === 'apps') await api('/api/apps/disconnect', { json: { app: app.slug, account: account.id } });
      else {
        const r = await fetch('/account/apps/disconnect.json', { method: 'POST', credentials: 'same-origin', body: new URLSearchParams({ id: account.id }) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Couldn't disconnect that account just now.");
      }
      if (account.via === 'imap') setMail((await api(`/api/connections/email?address=${encodeURIComponent(account.id)}`, { method: 'DELETE' })).emails);
      else { await loadMine(); load(); }
      setAsking('');
    } catch (e) { setProblem(e.message); }
    finally { setBusy(''); }
  };
  return html`<p class="np-help">Let Ken use Gmail, Calendar, Slack, Notion and 1,700+ more apps. Ken asks before it sends or changes anything.</p>
    ${say.get('connected') && html`<p class="np-note">${data?.connected?.find((c) => c.slug === say.get('connected'))?.name || 'The app'} is connected.</p>`}
    ${say.get('app_problem') && html`<p class="connect-error" role="alert">That didn't connect. Try again.</p>`}
    ${problem && html`<p class="connect-error" role="alert">${problem}</p>`}
    ${connected.length > 0 && html`<div class="app-accounts">${connected.map((a) => html`<div class="app-group">
      <div class="app-head">${logo(a, 22)}<span>${a.name}</span>${a.accounts.length > 1 && html`<small>${a.accounts.length} accounts</small>`}</div>
      ${a.accounts.map((x) => html`<div class="app-account"><span class="conn-on"><i></i><span class="app-account-name">${x.label}</span></span>${asking === x.id
        ? html`<span class="app-confirm"><button class="btn sm" disabled=${!!busy} onClick=${() => disconnect(a, x)}>${busy === 'off:' + x.id ? 'Disconnecting…' : 'Disconnect'}</button><button class="text-action" disabled=${!!busy} onClick=${() => setAsking('')}>Keep</button></span>`
        : html`<button class="text-action" disabled=${!!busy} aria-label=${`Disconnect ${a.name}: ${x.label}`} onClick=${() => setAsking(x.id)}>Disconnect</button>`}</div>`)}
      <button class="app-add" disabled=${!!busy} onClick=${() => add(a.slug)}>${busy === a.slug ? 'Opening…' : html`${Icon.plus} Add another account`}</button>
    </div>`)}</div>`}
    ${mine === null && data?.connected?.length > 0 && html`<p class="np-note" role="status">Finding out whose accounts these are…</p>`}
    <input class="apps-search" type="text" inputmode="search" placeholder="Search 1,700+ apps" aria-label="Search apps" value=${q} onInput=${(e) => setQ(e.target.value)}/>
    ${imap && html`<div class="apps-imap"><div class="app-head">${logo({ slug: 'imap' })}<span>Connect an inbox over IMAP</span></div>
      <${EmailForm} onDone=${(r) => { setMail(r.emails); setImap(false); }} onCancel=${() => setImap(false)}/></div>`}
    ${error ? html`<p class="connect-error" role="alert">${error} <button class="text-action" onClick=${() => load()}>Try again</button></p>` : !data ? html`<p class="np-note">Loading…</p>`
      : data.apps.length || imapShown ? html`<div class="apps-grid">${imapShown && html`<div class="apps-row">${logo({ slug: 'imap' })}<span>IMAP</span>${mail?.length
          ? html`<span class="conn-on"><i></i>Connected</span>` : html`<button class="btn sm" disabled=${imap} onClick=${() => setImap(true)}>Connect</button>`}</div>`}${data.apps.map((a) => html`<div class="apps-row" data-name=${a.name}><img src=${a.logo} alt="" width="22" height="22" loading="lazy"/><span class="app-name" tabindex="0" onMouseEnter=${fullName} onFocus=${fullName}>${a.name}</span>${a.connected
          ? html`<span class="conn-on"><i></i>Connected</span>` : html`<button class="btn sm" disabled=${!!busy} onClick=${() => connect(a.slug)}>${busy === a.slug ? 'Opening…' : 'Connect'}</button>`}</div>`)}</div>`
      : html`<p class="np-note">No app called "${q}".</p>`}`;
}

// Hosted Ken's Settings → Phone: two tabs, iPhone (the app: a code to scan from a computer, the App Store button on a
// phone) and Telegram. The code is the App Store link, made once (npx qrcode -t svg -e M -q 1 <link>).
const IPHONE_APP = 'https://apps.apple.com/app/id6830192469';
const IPHONE_QR = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 31 31" shape-rendering="crispEdges"><path fill="#ffffff" d="M0 0h31v31H0z"/><path stroke="#000000" d="M1 1.5h7m1 0h1m2 0h2m2 0h5m2 0h7M1 2.5h1m5 0h1m1 0h2m2 0h1m1 0h2m2 0h1m3 0h1m5 0h1M1 3.5h1m1 0h3m1 0h1m5 0h4m2 0h3m1 0h1m1 0h3m1 0h1M1 4.5h1m1 0h3m1 0h1m1 0h2m1 0h2m4 0h1m1 0h1m2 0h1m1 0h3m1 0h1M1 5.5h1m1 0h3m1 0h1m2 0h1m3 0h1m3 0h1m1 0h1m2 0h1m1 0h3m1 0h1M1 6.5h1m5 0h1m2 0h3m2 0h1m4 0h2m1 0h1m5 0h1M1 7.5h7m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h7M9 8.5h1m4 0h2m2 0h3M1 9.5h1m1 0h2m1 0h3m1 0h1m1 0h8m3 0h1m2 0h1m1 0h2M1 10.5h2m5 0h1m2 0h1m1 0h1m3 0h1m2 0h4m1 0h1m3 0h1M3 11.5h11m1 0h1m1 0h2m3 0h2m1 0h1m1 0h2M1 12.5h1m1 0h3m5 0h1m1 0h4m12 0h1M1 13.5h1m1 0h2m1 0h2m1 0h1m1 0h2m5 0h1m5 0h1m1 0h2M1 14.5h2m2 0h1m2 0h1m5 0h1m2 0h4m2 0h1m3 0h3M3 15.5h3m1 0h3m5 0h3m2 0h1m2 0h1m3 0h3M2 16.5h1m1 0h2m2 0h4m2 0h1m1 0h2m1 0h3m3 0h1m2 0h1M1 17.5h2m2 0h1m1 0h2m1 0h1m1 0h2m6 0h3m2 0h2m1 0h1M2 18.5h1m1 0h3m1 0h1m1 0h2m1 0h2m2 0h2m3 0h1m1 0h1m1 0h3M1 19.5h1m2 0h1m2 0h3m3 0h1m2 0h3m2 0h2m2 0h1m1 0h1M3 20.5h2m5 0h2m4 0h1m1 0h2m4 0h1m2 0h1M2 21.5h1m2 0h3m4 0h1m1 0h1m1 0h12M9 22.5h1m3 0h3m2 0h2m1 0h1m3 0h5M1 23.5h7m1 0h2m2 0h2m1 0h1m1 0h1m1 0h2m1 0h1m1 0h2m1 0h1M1 24.5h1m5 0h1m1 0h5m2 0h2m1 0h1m1 0h1m3 0h2m1 0h2M1 25.5h1m1 0h3m1 0h1m2 0h1m2 0h1m1 0h1m2 0h1m2 0h5m1 0h1m1 0h1M1 26.5h1m1 0h3m1 0h1m1 0h8m1 0h1m2 0h2m2 0h2m1 0h1M1 27.5h1m1 0h3m1 0h1m1 0h2m2 0h2m2 0h1m1 0h1m1 0h2m1 0h1m2 0h1m1 0h1M1 28.5h1m5 0h1m2 0h1m1 0h1m1 0h3m2 0h1m1 0h2m1 0h3m1 0h1M1 29.5h7m1 0h1m2 0h1m1 0h2m1 0h2m1 0h2m2 0h1m3 0h1"/></svg>';
function HostedPhone() {
  const [tab, setTab] = useState('iphone');
  return html`<div class="segmented phone-tabs" role="radiogroup" aria-label="Phone">${[['iphone', 'iPhone'], ['telegram', 'Telegram']].map(([id, label]) => html`<button role="radio" aria-checked=${tab === id} onClick=${() => setTab(id)}>${label}</button>`)}</div>
    ${tab === 'iphone' ? html`<p class="np-help">Ken on your iPhone: chat, get Ken's messages as notifications, and send it photos and files. Sign in with the same account.</p>
      <div class="tg-connect"><p class="np-help tg-scan">Scan this with your iPhone's camera.</p><div class="tg-qr" dangerouslySetInnerHTML=${{ __html: IPHONE_QR }}></div>
        <a class="btn primary" href=${IPHONE_APP} target="_blank" rel="noopener">Get it on the App Store</a></div>`
      : html`<${HostedTelegram}/>`}`;
}

// Hosted Ken's Settings → Telegram: a code to scan with the phone (a button on the phone itself), right here; it
// says Connected by itself once they press Start in Telegram.
function HostedTelegram() {
  const [linked, setLinked] = useState(null), [link, setLink] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const check = () => fetch('/account/telegram/status.json', { credentials: 'same-origin' }).then((r) => r.ok ? r.json() : Promise.reject()).then((r) => setLinked(r.linked));
  useEffect(() => { check().catch(() => setError("Couldn't check Telegram just now.")); }, []);
  useEffect(() => { if (!link || linked) return; const t = setInterval(() => check().catch(() => {}), 3000); const end = setTimeout(() => { clearInterval(t); setLink(null); setError('That code expired. Press Connect Telegram again.'); }, 1800000); return () => { clearInterval(t); clearTimeout(end); }; }, [link, linked]);
  const start = async () => {
    setBusy(true); setError('');
    try { const r = await fetch('/account/telegram/link.json', { method: 'POST', credentials: 'same-origin' }); const body = await r.json().catch(() => ({})); if (!r.ok || !body.url) throw new Error(body.error || "Telegram isn't available just now."); setLink(body); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const stop = async () => { setBusy(true); try { await fetch('/account/telegram/disconnect', { method: 'POST', credentials: 'same-origin' }); await check(); setLink(null); } catch { setError("Couldn't disconnect just now."); } finally { setBusy(false); } };
  return html`<p class="np-help">Talk to Ken in Telegram too. Same Ken, same memory, and it can message you there (your morning brief, reminders, finished jobs).</p>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    ${linked === null && !error ? html`<p class="np-note">Loading…</p>`
      : linked ? html`<div class="conn-list"><div><span class="conn-on"><i></i>Connected</span><button class="text-action" disabled=${busy} onClick=${stop}>Disconnect</button></div></div>`
      : link ? html`<div class="tg-connect"><p class="np-help tg-scan">Scan this with your phone's camera. Telegram opens; press Start.</p><div class="tg-qr" dangerouslySetInnerHTML=${{ __html: link.qr }}></div>
          <a class="btn primary" href=${link.url} target="_blank" rel="noopener">Open Telegram</a><p class="np-note" role="status">Waiting for you to press Start…</p></div>`
      : html`<button class="btn primary" disabled=${busy} onClick=${start}>${busy ? 'Getting a code…' : 'Connect Telegram'}</button>`}`;
}

// Ken's jobs, the same in Settings and the Brain: what each does in a line, when, and on or off.
// When a job runs, in words: "Every day", "Mon, Wed, Fri", or a one-time job's day ("Tomorrow", "Sat, Oct 31").
const DAY_NAMES = { mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday' };
function jobWhen(j) {
  if (j.date) {
    const day = new Date(j.date + 'T00:00'), today = new Date(); today.setHours(0, 0, 0, 0);
    const ahead = Math.round((day - today) / 86400000);
    return ahead === 0 ? 'Today' : ahead === 1 ? 'Tomorrow' : day.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }
  const days = String(j.days || 'daily').toLowerCase();
  if (days === 'daily') return 'Every day';
  if (days === 'weekdays') return 'Weekdays';
  const list = days.split(/[\s,]+/).map((d) => DAY_NAMES[d.slice(0, 3)]).filter(Boolean);
  return list.length === 1 ? list[0] + 's' : list.map((d) => d.slice(0, 3)).join(', ');
}

// To change what a job does, tell Ken.
function JobList({ jobs, change }) {
  const name = (j) => (!j.agent && JOB_NAMES[j.name]) || j.name.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const about = (j) => (j.agent_name ? j.agent_name + ' · ' : '') + ((!j.agent && JOB_HELP[j.name]) || j.summary || '');
  return html`<div class="job-list">${jobs.map((j) => html`<div class="job" key=${(j.agent || '') + ':' + j.name}>
    <div class="job-row">
      <div class="job-name"><b>${name(j)}</b><span>${about(j)}</span></div>
      <span class="job-when">${jobWhen(j)}</span>
      <input class="job-time" type="time" aria-label="Time" value=${j.time} disabled=${!j.on} onChange=${(e) => e.target.value && change(j, { time: e.target.value })}/>
      <input class="job-switch" type="checkbox" role="switch" aria-label=${j.on ? 'On' : 'Off'} checked=${j.on} onChange=${(e) => change(j, { on: e.target.checked })}/>
    </div>
  </div>`)}</div>`;
}

// Report a problem (the flag in the header, "Report it" on a page error, Settings → Send feedback): words, plus on
// Ken's site a screenshot or a few seconds of the screen, taken right here, or pictures from their files. The page's
// address, the browser and recent errors go with it. The Mac app sends the words.
const REPORT_MAX = 15 * 1024 * 1024, REPORT_FILES = 4, RECORD_SECONDS = 30;
const sizeLabel = (n) => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
// The browser asks which tab, window or screen to share, this tab first.
const shareScreen = () => navigator.mediaDevices.getDisplayMedia({ video: { displaySurface: 'browser', frameRate: 15 }, audio: false,
  preferCurrentTab: true, selfBrowserSurface: 'include', surfaceSwitching: 'exclude' });
const declined = (e) => e?.name === 'NotAllowedError' || e?.name === 'AbortError';
function ReportProblem({ onClose }) {
  const [text, setText] = useState(''), [email, setEmail] = useState(''), [files, setFiles] = useState([]);
  const [mode, setMode] = useState(''), [seconds, setSeconds] = useState(0);  // mode: '' | 'shot' (dialog hidden) | 'recording'
  const [progress, setProgress] = useState(null), [sent, setSent] = useState(false), [error, setError] = useState('');
  const stopRef = useRef(null), picker = useRef(null), filesRef = useRef(files);
  filesRef.current = files;
  useEffect(() => () => { stopRef.current?.(); filesRef.current.forEach((f) => f.url.startsWith('blob:') && URL.revokeObjectURL(f.url)); }, []);
  const capture = HOSTED && !!navigator.mediaDevices?.getDisplayMedia;
  // Pictures preview as data: URLs (the page only shows images from itself or data:); recordings play from blob:.
  const preview = (file) => file.type.startsWith('video/') ? Promise.resolve(URL.createObjectURL(file))
    : new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => ok(''); r.readAsDataURL(file); });
  const add = async (list) => {
    const ready = await Promise.all(list.map(async (file) => ({ file, url: await preview(file) })));
    setFiles((now) => [...now, ...ready].slice(0, REPORT_FILES));
  };
  const remove = (i) => setFiles((now) => { if (now[i].url.startsWith('blob:')) URL.revokeObjectURL(now[i].url); return now.filter((_, k) => k !== i); });
  const total = files.reduce((n, f) => n + f.file.size, 0), full = files.length >= REPORT_FILES;
  const stamp = () => new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-');
  // The screen without this dialog on it: hidden while the picture is taken.
  const screenshot = async () => {
    setError('');
    let stream;
    try {
      stream = await shareScreen();
      setMode('shot');
      const video = Object.assign(document.createElement('video'), { srcObject: stream, muted: true, playsInline: true });
      await video.play();
      await new Promise((ok) => setTimeout(ok, 500));
      const canvas = Object.assign(document.createElement('canvas'), { width: video.videoWidth, height: video.videoHeight });
      canvas.getContext('2d').drawImage(video, 0, 0);
      const blob = await new Promise((ok) => canvas.toBlob(ok, 'image/png'));
      await add([new File([blob], `screenshot-${stamp()}.png`, { type: 'image/png' })]);
    } catch (e) { if (!declined(e)) setError('Couldn’t take a screenshot. Add one from your files instead.'); }
    finally { stream?.getTracks().forEach((t) => t.stop()); setMode(''); }
  };
  // Up to 30 seconds of the screen while they show what went wrong; a small Stop pill stays on top.
  const record = async () => {
    setError('');
    let stream;
    try { stream = await shareScreen(); }
    catch (e) { if (!declined(e)) setError('Couldn’t record the screen. Add a video from your files instead.'); return; }
    const type = ['video/mp4', 'video/webm;codecs=vp9', 'video/webm'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
    const rec = new MediaRecorder(stream, { ...(type && { mimeType: type }), videoBitsPerSecond: 1500000 });
    const parts = [];
    let timer;
    rec.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
    rec.onstop = () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      stopRef.current = null;
      const kind = (rec.mimeType || type || 'video/webm').split(';')[0];
      if (parts.length) add([new File(parts, `recording-${stamp()}.${kind === 'video/mp4' ? 'mp4' : 'webm'}`, { type: kind })]);
      setMode('');
    };
    const stop = () => { if (rec.state !== 'inactive') rec.stop(); };
    stopRef.current = stop;
    stream.getVideoTracks()[0].addEventListener('ended', stop);  // "Stop sharing" in the browser's own bar
    const began = Date.now();
    setSeconds(0); setMode('recording');
    timer = setInterval(() => { const s = Math.floor((Date.now() - began) / 1000); setSeconds(s); if (s >= RECORD_SECONDS) stop(); }, 250);
    rec.start(1000);
  };
  const send = async () => {
    setError(''); setProgress(0);
    try {
      if (HOSTED) {
        const fd = new FormData();
        fd.append('text', text);
        fd.append('context', JSON.stringify({ page: location.href, browser: navigator.userAgent, screen: `${innerWidth}×${innerHeight}`, errors: RECENT_ERRORS }));
        for (const f of files) fd.append('file', f.file, f.file.name);
        await sendWithProgress('/api/feedback', fd, setProgress);
      } else await api('/api/feedback', { json: { text, email } });
      setSent(true);
    } catch (e) { setError(e.message); } finally { setProgress(null); }
  };
  if (mode === 'shot') return null;
  if (mode === 'recording') return html`<div class="rec-pill" role="status"><i></i><span>Recording ${mmss(seconds)} / ${mmss(RECORD_SECONDS)}</span><button class="btn sm primary" onClick=${() => stopRef.current?.()}>Stop</button></div>`;
  const glyph = (d) => html`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${d}/></svg>`;
  return html`<${Modal} title=${sent ? 'Thanks, it’s sent' : 'Report a problem'} onClose=${onClose} className="report" closeX>${sent
    ? html`<p class="np-help">It’s on its way to the people who make Ken.${HOSTED ? ' We reply to the email you signed in with.' : ''}</p>
      <div class="row np-actions"><button class="btn primary" onClick=${onClose}>Done</button></div>`
    : html`<p class="np-help">A bug, an idea, something you’d love Ken to do.${capture ? ' A screenshot or a few seconds of your screen helps us see it.' : ''}</p>
      <textarea class="np-input feedback-text" placeholder="What happened?" value=${text} onInput=${(e) => setText(e.target.value)}></textarea>
      ${!HOSTED && html`<input class="np-input" type="email" placeholder="Your email, if you'd like a reply (optional)" value=${email} onInput=${(e) => setEmail(e.target.value)}/>`}
      ${HOSTED && html`<div class="report-attach">
        ${capture && html`<button class="btn sm" disabled=${full} onClick=${screenshot}>${glyph('M4 8h3l2-3h6l2 3h3v11H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z')}Screenshot</button>
          <button class="btn sm" disabled=${full} onClick=${record}>${glyph('M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z')}Record screen</button>`}
        <button class="btn sm" disabled=${full} onClick=${() => picker.current.click()}>${glyph('M21 11l-8.5 8.5a5 5 0 0 1-7-7L14 4a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15.5 7')}${capture ? 'Add a file' : 'Add a screenshot or video'}</button>
        <input ref=${picker} type="file" accept="image/*,video/*" multiple hidden onChange=${(e) => { add([...e.target.files]); e.target.value = ''; }}/>
      </div>
      ${files.length > 0 && html`<div class="report-files">${files.map((f, i) => html`<figure key=${f.file.name + f.file.size}>${f.file.type.startsWith('video/')
        ? html`<video src=${f.url} muted playsinline controls preload="metadata"></video>` : html`<img src=${f.url} alt=${f.file.name}/>`}
        <figcaption>${f.file.type.startsWith('video/') ? 'Recording' : 'Picture'} · ${sizeLabel(f.file.size)}</figcaption>
        <button class="report-x" aria-label=${'Remove ' + f.file.name} onClick=${() => remove(i)}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></figure>`)}</div>`}
      ${total > REPORT_MAX && html`<p class="connect-error" role="alert">That’s ${sizeLabel(total)}, more than 15 MB. Remove something or record a shorter clip.</p>`}
      <p class="np-note">Sent with it: this page’s address, your browser and any recent errors on this page.</p>`}
      ${progress !== null && files.length > 0 && html`<div class="upload-bar" role="progressbar" aria-valuenow=${Math.round(progress * 100)}><span style=${`width:${Math.round(progress * 100)}%`}></span></div>`}
      ${error && html`<p class="connect-error" role="alert">${error}</p>`}
      <div class="row np-actions"><button class="btn ghost" onClick=${onClose}>Cancel</button><button class="btn primary" disabled=${progress !== null || (!text.trim() && !files.length) || total > REPORT_MAX} onClick=${send}>${progress === null ? 'Send' : files.length ? `Sending… ${Math.round(progress * 100)}%` : 'Sending…'}</button></div>
      <p class="np-note">Anything urgent? Email <a class="text-action" href=${'mailto:' + HELP_EMAIL}>${HELP_EMAIL}</a>${FOUNDER_TELEGRAM && html` or <a class="text-action" href=${'https://t.me/' + FOUNDER_TELEGRAM} target="_blank" rel="noopener">message the founder on Telegram</a>`}.</p>`}<//>`;
}

// Settings → AI, under their own Claude or ChatGPT: how much of each limit is used and when it resets. The numbers
// come with Ken's replies, so showing them costs nothing; before the first reply there's nothing to show yet.
function PlanLimits({ windows, label }) {
  if (!windows.length) return html`<div class="ai-limits"><span class="ai-note">How much of your ${label} plan is used shows here after Ken’s next reply.</span></div>`;
  return html`<div class="ai-limits">${windows.map((w) => html`<div class=${'ai-limit' + (w.near ? ' near' : '')}>
    <span class="ai-limit-name">${w.label}</span>
    <span class="ai-bar" role="meter" aria-label=${`${w.label} limit used`} aria-valuemin="0" aria-valuemax="100" aria-valuenow=${Math.round(w.pct ?? 0)}><i style=${`width:${Math.min(100, Math.round(w.pct ?? 0))}%`}></i></span>
    <span class="ai-limit-text">${w.pct == null ? 'Near the limit' : `${Math.round(w.pct)}%`} · resets ${w.reset}</span></div>`)}</div>`;
}

// An app name too long for its tile (past two lines) ends in "…"; hovering or tapping it shows the whole name.
const fullName = (e) => { const el = e.currentTarget; el.parentElement.classList.toggle('cut', el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth); };

function Settings({ onClose, onTab, toast, onAddTool, setup, onConnected, meta = {}, tab, brain }) {
  const [offAsk, setOffAsk] = useState(false), [me, setMe] = useState(null);
  useEffect(() => { if (HOSTED) fetch('/account/me.json', { credentials: 'same-origin' }).then((r) => r.ok ? r.json() : null).then(setMe).catch(() => {}); }, []);
  const [appearance, setAppearance] = useState(() => window.kenTheme.preference);
  useEffect(() => {
    const changed = () => setAppearance(window.kenTheme.preference);
    window.addEventListener('ken-theme-change', changed);
    return () => window.removeEventListener('ken-theme-change', changed);
  }, []);
  const [error, setError] = useState(''), [reconnect, setReconnect] = useState(() => (/^connect-(claude|codex)$/.test(tab || '') ? tab.slice(8) : '')), [update, setUpdate] = useState(null);
  useEffect(() => { window.kenDesktop?.updateStatus?.().then(setUpdate).catch(() => {}); return window.kenDesktop?.onUpdateState?.((s) => {
    if (s.state === 'ready') setUpdate((u) => ({ ...u, ready: s.version }));
    if (s.state === 'channel') setUpdate((u) => ({ ...u, channel: s.channel, ready: null }));
  }); }, []);

  const setTab = onTab;
  useEffect(() => { const esc = (e) => { if (e.key === 'Escape' && !document.querySelector('.modal')) onClose(); }; addEventListener('keydown', esc); return () => removeEventListener('keydown', esc); }, [onClose]);
  const [tools, setTools] = useState(null);
  const [privacy, setPrivacy] = useState(null), [limits, setLimits] = useState(null);
  useEffect(() => { api('/api/limits').then(setLimits).catch(() => {}); }, []);  // their plans' limits, from replies (no AI call)
  useEffect(() => { api('/api/privacy').then(setPrivacy).catch(() => {}); if (!HOSTED) api('/api/about/home').then((a) => setTools(a.tools)).catch((e) => setTools(e)); }, []);
  const share = async (on) => { setError(''); try { setPrivacy(await api('/api/privacy', { method: 'PUT', json: { share: on } })); } catch (e) { setError(e.message); } };
  const row = (icon, title, text, right, body) => html`<div class="conn"><div class="conn-row"><span class="conn-icon">${icon}</span><div class="conn-text"><b>${title}</b><span>${text}</span></div><div class="conn-right">${right}</div></div>${body}</div>`;
  const on = (label) => html`<span class="conn-on"><i></i>${label}</span>`;
  const icon = (d) => html`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d=${d}/></svg>`;
  // One calm page, every section in view, the same in the Mac app and the browser; the parts that only make sense on
  // one of them show only there (HOSTED: plan, apps and Telegram on Ken's site; the Mac: Phone and updates).
  // #/settings/<section> scrolls to that section.
  // Tabs, one section at a time (#/settings/<tab> opens that one). Plan, Apps and Telegram are Ken's site only.
  const tabsList = [...(HOSTED ? [['plan', 'Plan']] : []), ['claude', 'AI'], ...(HOSTED ? [['apps', 'Apps']] : []), ['phone', 'Phone'], ...(HOSTED ? [] : [['tools', 'Tools']]), ['account', 'Account']];  // Ken on the site: Apps covers it
  const wanted = tab && tab.startsWith('connect-') ? 'claude' : ['privacy', 'appearance', 'general'].includes(tab) ? 'account' : tab;
  const current = wanted === 'feedback' ? 'account' : tabsList.some(([id]) => id === wanted) ? wanted : tabsList[0][0];
  const section = (id, title, body) => id !== current ? '' : html`<section class="sp-section" id=${'set-' + id}><h2>${title}</h2>${body}</section>`;
  // Until Ken has said what it is (in the cloud or on a Mac), its sections aren't known: a calm line, not the wrong ones.
  if (!Object.keys(meta).length) return html`<div class="settings-page" role="region" aria-label="Settings"><div class="sp-wrap"><header class="sp-top"><span class="sp-brand"><img src="/ken.svg" alt="" width="26" height="26"/>Ken</span><button class="btn primary sm" onClick=${onClose}>Back to Ken</button></header><h1 class="sp-title">Settings</h1><${WaitingForKen}/></div></div>`;
  return html`<div class="settings-page" role="region" aria-label="Settings"><div class="sp-wrap">
  <header class="sp-top"><span class="sp-brand"><img src="/ken.svg" alt="" width="26" height="26"/>Ken</span><button class="btn primary sm" onClick=${onClose}>Back to Ken</button></header><h1 class="sp-title">Settings</h1>
  <nav class="sp-tabs" role="tablist" aria-label="Settings">${tabsList.map(([id, label]) => html`<button role="tab" aria-selected=${current === id} class=${current === id ? 'on' : ''} onClick=${() => { setTab(id); setError(''); }}>${label}</button>`)}</nav>
  ${HOSTED && current === 'plan' && (meta.usage ? html`<${PlanUsage} usage=${meta.usage} accountUrl=${meta.account_url}/>` : meta.account_url && html`<p class="np-note"><a class="text-action" href="/account/card?back=ken">Plan and billing</a></p>`)}
  ${section('claude', 'AI that Ken uses', html`<p class="np-help">${HOSTED ? 'Ken runs on your own Claude or ChatGPT when one is connected, and on Ken AI credits when not.' : 'Ken runs on your Claude or ChatGPT subscription.'}</p>
    <div class="conn-list ai-uses">${(() => {
      // Claude and ChatGPT only ever mean their own subscription; Ken AI credits are their own row (hosted only).
      // Exactly one row is "in use": their own subscription (the chat's chosen brain if both), else Ken AI credits.
      const own = Object.fromEntries(['claude', 'codex'].map((id) => { const a = setup?.providers?.[id] || {}; return [id, !!a.connected && a.how !== 'included']; }));
      const inUse = own[brain] ? brain : own.claude ? 'claude' : own.codex ? 'codex' : HOSTED ? 'credits' : '';
      const name = (id, label) => html`<span class="ai-name">${inUse === id ? html`<i class="ai-dot" aria-hidden="true"></i>` : ''}${label}</span>`;
      const rows = [['claude', 'Claude'], ['codex', 'ChatGPT']].map(([id, label]) => html`<div>${name(id, label)}<span class="ai-state">${own[id] ? `Connected${inUse === id ? ' · in use' : ''}` : 'Not connected'}</span>
        <span class="ai-actions"><button class="text-action" onClick=${() => setReconnect(id)}>${own[id] ? 'Reconnect' : `Connect ${label}`}</button>${HOSTED && own[id] && html`<button class="text-action" onClick=${() => api('/api/setup', { json: { action: 'disconnect', backend: id } }).then((r) => { onConnected(r); toast(`${label} disconnected.`); }).catch((e) => setError(e.message))}>Disconnect</button>`}</span>
        ${own[id] && limits && html`<${PlanLimits} windows=${limits[id] || []} label=${label}/>`}</div>`);
      const usage = meta.usage || {};
      if (HOSTED && (usage.credits_left || usage.free)) rows.push(html`<div>${name('credits', 'Ken AI credits')}<span class="ai-state">${usage.credits_left ? `${usage.credits_left} left` : ''}${inUse === 'credits' ? `${usage.credits_left ? ' · ' : ''}in use` : ''}</span>
        <span class="ai-actions">${usage.topup_url ? html`<a class="text-action" href=${usage.topup_url}>Add credits</a>` : usage.free && html`<a class="text-action" href="/account/plans">Get more</a>`}</span></div>`);
      return rows;
    })()}</div>
    ${reconnect && html`<${Connect} key=${reconnect} backend=${reconnect} onDone=${(r) => { setReconnect(''); onConnected(r); toast('Account connected.'); }}/>`}`)}
  ${HOSTED && section('apps', 'Apps', html`<${HostedApps}/>`)}
  ${section('phone', 'Phone', HOSTED ? html`<${HostedPhone}/>`
    : html`<div class="phone-body"><p class="np-help">Text or talk to Ken from anywhere with Telegram. Same Ken, same chat.</p><${PhoneSteps} toast=${toast}/></div>`)}
  ${section('tools', 'Tools', html`<p class="np-help">What Ken can use on this computer and online.</p>${tools instanceof Error ? html`<p class="np-note">${tools.message}</p>` : tools ? html`<${ToolList} switches=${false} tools=${tools} onAddTool=${(name) => { onClose(); onAddTool(name); }}/>` : html`<p class="np-note">Loading…</p>`}`)}
  ${section('account', 'Account', html`<div class="tool-list">
    ${me?.signedIn && row(icon('M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0'), me.name || 'Signed in', me.email,
      meta.logout_url && html`<a class="btn sm light" href=${meta.logout_url}>Sign out</a>`)}
    ${row(icon('M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z'), 'Appearance', 'Light, dark, or the same as your computer.',
      html`<div class="segmented" role="radiogroup" aria-label="Appearance">${[['system', 'Auto'], ['light', 'Light'], ['dark', 'Dark']].map(([id, label]) => html`<button role="radio" aria-checked=${appearance === id} onClick=${() => window.kenTheme.setPreference(id)}>${label}</button>`)}</div>`)}
    ${privacy && row(icon('M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z'), 'Share anonymous crash reports and usage',
      html`Helps fix bugs and shows which features get used: counts like “a job ran” and how much AI each one used. Never what you say, your files or your logins. <a class="text-action" href="https://kencomputer.dev/privacy" target="_blank" rel="noopener">Exactly what's sent</a>`,
      html`<input class="job-switch" type="checkbox" role="switch" aria-label=${privacy.share ? 'On' : 'Off'} checked=${privacy.share} onChange=${(e) => share(e.target.checked)}/>`)}
    ${row(icon('M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z'), 'Send feedback', 'A bug, an idea, something you’d love Ken to do. It goes straight to the people who make Ken.',
      html`<button class="btn sm light" onClick=${() => openReport?.()}>Write</button>`)}
  </div>
  <div class="tool-list off-row">${row(icon('M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0'), 'Turn Ken off', 'Stops everything Ken is doing right now, mid-step. No replies, briefs or jobs until you turn it back on.',
    !offAsk && html`<button class="btn sm off-btn" onClick=${() => setOffAsk(true)}>Turn off</button>`,
    offAsk && html`<div class="off-ask"><p>Your plan, memory and files are kept. Turn Ken on again from the app any time.</p>
      <div class="row np-actions"><button class="btn ghost" onClick=${() => setOffAsk(false)}>Cancel</button><button class="btn danger" onClick=${() => api('/api/power', { json: { on: false } }).then(onClose, (e) => setError(e.message))}>Turn Ken off</button></div></div>`)}</div>`)}
  ${error && html`<p class="connect-error" role="alert">${error}</p>`}
  ${update && html`<footer class="sp-foot"><span class="settings-version">Ken ${update.version} · ${update.ready
      ? html`<button class="text-action" onClick=${() => window.kenDesktop.installUpdate()}>Restart to update to ${update.ready}</button>`
      : html`<button class="text-action" onClick=${() => window.kenDesktop.checkForUpdates()}>Check for updates</button>`}</span></footer>`}
  </div></div>`;
}
// Waiting for Ken's computer (it's waking up or restarting into an update): say so, and never wait forever.
function WaitingForKen() {
  const [secs, setSecs] = useState(0);
  useEffect(() => { const t = setInterval(() => setSecs((n) => n + 1), 1000); return () => clearInterval(t); }, []);
  if (secs < 3) return html`<p class="np-note" role="status">Loading…</p>`;
  if (secs < 45) return html`<p class="np-note" role="status">Ken's computer is waking up. This usually takes under a minute…</p>`;
  return html`<p class="connect-error" role="alert">Ken's computer isn't answering yet.</p><button class="btn primary" onClick=${() => location.reload()}>Try again</button>`;
}
// What's left of the plan, as the service hosting this Ken reports it: one line each (awake time, AI, app actions).
function PlanUsage({ usage, accountUrl }) {
  // Hosted Ken: straight to Stripe's billing page (plan, card, invoices), whose Return comes back to Ken.
  // Hosted Ken: Plan & billing is its own small page on the site (plans, added credits, card and invoices), one tap back.
  const manage = HOSTED ? '/account/plans' : usage.manage_url || accountUrl;
  const share = (l) => { const n = (t) => parseFloat(String(t).replace(/[^0-9.]/g, '')); const v = n(l.left) / n(l.of); return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : null; };
  return html`<div class="plan-usage">${usage.plan && html`<b>${usage.plan}</b>`}
    ${usage.lines.filter((l) => !(usage.added && l.label === 'Added credits')).map((l) => { const v = share(l); return html`<div class=${'meter' + (l.low ? ' low' : '')}><div class="meter-top"><span>${l.label}</span><span>${l.left}${l.of ? ` of ${l.of}` : ''} left</span></div>${v !== null && html`<div class="bar"><i style=${{ width: Math.round(v * 100) + '%' }}></i></div>`}</div>`; })}
    ${usage.added && html`<p class="plan-added">+ ${usage.added} added credits</p>`}
    ${usage.note && html`<p class="np-note">${usage.note}</p>`}
    <div class="plan-actions">${usage.topup_url && html`<a class="btn primary" href=${usage.topup_url}>Add Ken AI credits</a>`}
    ${manage && html`<a class="btn" href=${manage}>Plan & billing</a>`}</div></div>`;
}
const HELP_EMAIL = 'help@kencomputer.dev';  // "Anything urgent? Email …" under Send feedback
const FOUNDER_TELEGRAM = 'prasann16';  // "Message the founder on Telegram" under Send feedback; empty hides it
const JOB_NAMES = { 'morning-brief': 'Morning brief', 'nightly-review': 'Nightly review' };
const JOB_HELP = { 'morning-brief': 'What matters today, sent to you each morning.', 'nightly-review': 'Files the day into Ken’s memory and tells you what it saved.' };

// Hosted Ken: files too big for Telegram to pass on, sent here instead (the link Ken replied with in Telegram,
// <site>/send/<token>, which opens #/send/<token>). A whole page, so nothing else gets in the way: pick them all,
// watch them go up, and Ken carries on in Telegram.
function SendBigFile({ token, onClose }) {
  const [info, setInfo] = useState(null), [error, setError] = useState(''), [progress, setProgress] = useState(null), [done, setDone] = useState(false);
  useEffect(() => { api(`/api/telegram-files/${token}`).then(setInfo).catch((e) => setError(e.message)); }, []);
  const files = info?.files?.length ? info.files : info ? [info] : [];
  const nouns = new Set(files.map((f) => /video/.test(f.kind || '') ? 'video' : /recording|voice/.test(f.kind || '') ? 'recording' : 'file'));
  const noun = nouns.size === 1 ? [...nouns][0] : 'file', many = files.length > 1;
  const pick = async (list) => {
    if (!list.length) return;
    setError(''); setProgress(0);
    try {
      const names = await upload('home', list, true, setProgress);
      await api(`/api/telegram-files/${token}`, { json: { files: names } });
      setDone(true);
    } catch (e) { setError(e.message); setProgress(null); }
  };
  const pct = Math.round((progress || 0) * 100);
  return html`<div class="settings-page send-page" role="region" aria-label="Send files to Ken"><div class="sp-wrap">
    <header class="sp-top"><span class="sp-brand"><img src="/ken.svg" alt="" width="26" height="26"/>Ken</span><button class="btn sm ghost" onClick=${onClose}>Close</button></header>
    ${done ? html`<h1 class="sp-title">Ken has them</h1><p class="np-help">Ken is on it, and will answer you in Telegram.</p><div class="row np-actions"><button class="btn primary" onClick=${onClose}>Done</button></div>`
      : error && !info ? html`<h1 class="sp-title">This link doesn't work</h1><p class="connect-error" role="alert">${error}</p>`
      : !info ? html`<p class="np-note">Loading…</p>`
      : html`<h1 class="sp-title">Send ${many ? `your ${files.length} ${noun}s` : `the ${noun}`} here</h1>
        <p class="np-help">Telegram can't pass on files this big. Choose ${many ? 'them all' : 'it'} here, at full quality, and Ken carries on with what you asked${info.caption ? `: “${info.caption}”` : ''}.</p>
        ${files.some((f) => f.name) && html`<ul class="send-list">${files.map((f) => html`<li><span>${f.name}</span><small>${Math.round((f.size || 0) / 1048576)} MB</small></li>`)}</ul>`}
        ${progress === null ? html`<label class="btn primary big-file-pick">Choose ${many ? `the ${noun}s` : `the ${noun}`}<input type="file" multiple hidden onChange=${(e) => pick([...e.target.files])}/></label>`
          : html`<div class="send-progress" role="status"><div class="upload-bar"><span style=${`width:${pct}%`}></span></div><p class="np-note">Sending… ${pct}% · keep this page open until it's done.</p></div>`}
        ${error && html`<p class="connect-error" role="alert">${error}</p>`}`}
  </div></div>`;
}

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

const EXAMPLES = ['Turn my video into reels', 'Find a laptop under $1,000', 'Send me news every morning'];
const TOOL_NAMES = { browser: 'Chrome', email: 'Email', calendar: 'Calendar' };

// Ken's Brain: who it is (SOUL.md), its memory notes, tools, keys, jobs and files. A calm overview
// of one line each; open a line to see and edit that part.
function About({ pid, onClose, onPreview, page, onAddTool }) {
  const [a, setA] = useState(null), [conn, setConn] = useState(null), [jobs, setJobs] = useState([]), [section, setSection] = useState(null);
  const [edit, setEdit] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [skill, setSkill] = useState(null);
  const load = () => {
    api(`/api/about/${pid}`).then(setA).catch((e) => setError(e.message));
    api('/api/connections').then(setConn).catch(() => {});
    api('/api/jobs').then((all) => setJobs(all.filter((j) => (j.agent || 'home') === pid))).catch(() => {});
  };
  useEffect(load, []);
  const save = async (fn) => { setBusy(true); setError(''); try { setA(await fn()); setEdit(null); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  // A note opens formatted to read; Edit turns it into text to change.
  const openNote = (name) => api(`/api/about/${pid}/memory/${encodeURIComponent(name)}`).then((n) => setEdit({ kind: 'note', name, text: n.text, saved: n.text, reading: true })).catch((e) => setError(e.message));
  const noteName = (n) => (n.trim().replace(/[^\w .-]/g, '').replace(/\.md$/i, '') || 'note') + '.md';
  const job = async (j, change) => { try { const all = await api(`/api/jobs/${encodeURIComponent(j.name)}`, { method: 'PATCH', json: { ...change, agent: j.agent } }); setJobs(all.filter((x) => (x.agent || 'home') === pid)); return true; } catch (e) { setError(e.message); return false; } };
  const jobName = (j) => (pid === 'home' && JOB_NAMES[j.name]) || j.name.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const frame = (title, body) => page
    ? html`<div class="knows-page"><div class="knows-inner">${title && html`<h1>${title}</h1>`}${body}</div></div>`
    : html`<${Modal} wide title=${title || `About ${a ? a.name : ''}`} onClose=${edit ? () => setEdit(null) : onClose}>${body}<//>`;
  const back = (to, label) => html`<button class="text-action brain-back" onClick=${to}>‹ ${label}</button>`;
  if (skill) return frame(skill.name, html`<div class="brain-section">${back(() => setSkill(null), 'Skills')}
    ${skill.about && html`<p class="np-help">${skill.about}</p>`}
    <div class="about-context">${skill.text ? doc(skill.text.replace(/\[([^\]]+)\]\((?!https?:)[^)]*\)/g, '$1')) : 'This skill has no main page.'}</div>
    ${skill.parts.length > 0 && html`<p class="np-help skill-parts">Also includes: ${skill.parts.join(', ')}</p>`}</div>`);
  if (edit?.reading) return frame(edit.name.replace(/\.md$/, ''), html`<div class="brain-section">${back(() => setEdit(null), 'Memory')}
    <div class="brain-actions"><button class="text-action" onClick=${() => setEdit({ ...edit, reading: false })}>Edit</button></div>
    <div class="about-context">${edit.text.trim() ? doc(edit.text) : 'Empty note.'}</div>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}</div>`);
  if (edit) return frame(edit.kind === 'context' ? (pid === 'home' ? 'Who Ken is' : 'Context') : edit.isNew ? 'New note' : edit.name.replace(/\.md$/, ''), html`
    ${edit.isNew && html`<input class="np-input" placeholder="Note name, like people" value=${edit.name} onInput=${(e) => setEdit({ ...edit, name: e.target.value })}/>`}
    <textarea class="np-input about-editor" value=${edit.text} onInput=${(e) => setEdit({ ...edit, text: e.target.value })}></textarea>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    <div class="row np-actions">
      ${edit.kind === 'note' && !edit.isNew && html`<button class="btn ghost" style="margin-right:auto" disabled=${busy} onClick=${() => save(() => api(`/api/about/${pid}/memory/${encodeURIComponent(edit.name)}`, { method: 'DELETE' }))}>Delete note</button>`}
      <button class="btn ghost" onClick=${() => setEdit(edit.kind === 'note' && !edit.isNew ? { ...edit, text: edit.saved, reading: true } : null)}>Cancel</button>
      <button class="btn primary" disabled=${busy || (edit.isNew && !edit.name.trim())} onClick=${() => save(() => edit.kind === 'context'
        ? api(`/api/about/${pid}/context`, { method: 'PUT', json: { text: edit.text } })
        : api(`/api/about/${pid}/memory/${encodeURIComponent(edit.isNew ? noteName(edit.name) : edit.name)}`, { method: 'PUT', json: { text: edit.text } }))}>Save</button>
    </div>`);
  if (!a) return frame(null, html`<p class="np-help">${error || 'Loading…'}</p>`);
  const plain = (t) => t.replace(/^\s*(?:[-*•]|\d+\.)\s+/, '').replace(/[*_`#>]/g, '').trim();  // a list item reads as a sentence
  const firstLine = a.context.split('\n').filter((l) => !/^\s*#/.test(l)).map(plain).find((l) => l && !/^You are\b/.test(l) && !/^\(.*\)$/.test(l)) || 'Nothing yet';
  const connected = conn ? { browser: conn.chrome, email: conn.emails.length > 0, calendar: conn.calendars.length > 0 } : {};
  const on = a.builtin.filter((t) => connected[t] && a.tools.use.includes(t)).map((t) => TOOL_NAMES[t]).concat(Object.keys(a.tools.mcp));
  const off = conn ? a.builtin.filter((t) => !connected[t]).map((t) => TOOL_NAMES[t]) : [];
  const count = (n, one) => `${n} ${one}${n === 1 ? '' : 's'}`;
  const parts = [
    { key: 'soul', title: pid === 'home' ? 'Who Ken is' : 'Context', summary: firstLine, body: html`
        <div class="brain-actions"><button class="text-action" onClick=${() => setEdit({ kind: 'context', text: a.context })}>Edit</button></div>
        <div class="about-context">${a.context.trim() ? doc(a.context) : 'Nothing yet.'}</div>` },
    { key: 'memory', title: pid === 'home' ? 'What Ken remembers' : 'Memory', summary: a.memory.length ? count(a.memory.length, 'note') : 'Nothing yet', body: html`
        <div class="brain-actions"><button class="text-action" onClick=${() => setEdit({ kind: 'note', isNew: true, name: '', text: '' })}>New note</button></div>
        ${a.memory.length ? a.memory.map((n) => html`<button class="about-item" onClick=${() => openNote(n.name)}><b>${n.name.replace(/\.md$/, '')}</b><span>${n.about}</span></button>`) : html`<p class="np-note">No notes yet. Ken writes them as it learns about you.</p>`}` },
    pid !== 'home' && { key: 'tools', title: 'Tools', summary: [on.join(', ') || 'Nothing connected', off.length && off.join(', ') + ' not connected'].filter(Boolean).join(' · '), body: html`
        <${ToolList} tools=${a.tools} onUse=${(t, yes) => save(() => api(`/api/agents/${pid}/tools`, { method: 'PUT', json: { use: yes ? [...a.tools.use, t] : a.tools.use.filter((x) => x !== t) } }))} onAddTool=${onAddTool}/>` },
    { key: 'keys', title: 'Accounts & passwords', summary: a.keys.length ? count(a.keys.length, 'key') : 'None yet', body: html`<${Keys} pid=${pid} initial=${a.keys}/>` },
    pid === 'home' && { key: 'skills', title: 'Skills', summary: a.skills.some((s) => !s.app) ? a.skills.filter((s) => !s.app).map((s) => s.name).join(', ') : 'None yet', body: html`
        <p class="np-help">Skills teach Ken a specific job, like making videos. To add one, just ask Ken in the chat.</p>
        ${a.skills.filter((s) => !s.app).map((s) => html`<div class="skill-row"><button class="skill-open" onClick=${() => api(`/api/skills/${encodeURIComponent(s.id)}`).then(setSkill).catch((e) => setError(e.message))}><b>${s.name}</b><span>${s.about}</span></button><button class="text-action" disabled=${busy} onClick=${() => save(() => api(`/api/skills/${encodeURIComponent(s.id)}`, { method: 'DELETE' }).then(() => api(`/api/about/${pid}`)))}>Remove</button></div>`)}` },
    jobs.length > 0 && { key: 'jobs', title: 'Scheduled jobs', summary: jobs.filter((j) => j.on).map((j) => `${jobName(j)} ${j.time}`).join(' · ') || 'All off', body: html`<${JobList} jobs=${jobs} change=${job}/>` },
  ].filter(Boolean);
  const open = parts.find((p) => p.key === section);
  return frame(null, html`
    ${open ? html`<div class="brain-section">${back(() => { setSection(null); load(); }, 'Brain')}<h2>${open.title}</h2>${open.body}</div>`
      : html`${page && html`<h1>Brain</h1><p class="brain-intro">Everything Ken knows and does on its own. Ken keeps this up to date as you talk; you can change anything here.</p>`}
        <div class="brain-cards">${parts.map((p) => html`<button class="brain-card" onClick=${() => setSection(p.key)}><b>${p.title}</b><span>${p.summary}</span><i aria-hidden="true">Open ›</i></button>`)}</div>`}
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

function NewAgent({ onClose, onCreated, setup }) {
  const [backend, setBackend] = useState(setup?.backend || 'claude');
  const [name, setName] = useState(''), [job, setJob] = useState(''), [path, setPath] = useState(''), [more, setMore] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const pick = async () => { const p = await window.kenDesktop.pickFolder(); if (p) { setPath(p); if (!name) setName(p.split('/').pop()); } };
  const ready = name.trim() && job.trim() && !busy && setup?.providers?.[backend]?.connected;
  const create = async () => {
    setBusy(true); setError('');
    try { onCreated(await api('/api/chats', { json: { name: name.trim(), context: job.trim(), path, backend, setup: true } })); } catch (e) { setError(e.message); setBusy(false); }
  };
  return html`<${Modal} title="New agent" onClose=${onClose}>
    <p class="np-help">An agent does one job for you, with its own chat, memory, schedule and tools. Tell it what you want; it sets itself up.</p>
    <label class="np-label">Brain<select class="np-input" aria-label="Agent brain" value=${backend} onChange=${(e)=>setBackend(e.target.value)}>${[['claude','Claude'],['codex','Codex']].map(([id,label])=>html`<option value=${id}>${label}${setup?.providers?.[id]?.connected ? '' : ' — connect in Settings'}</option>`)}</select></label>
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
  const [modelCatalog,setModelCatalog]=useState({});
  const loadModelCatalog=()=>api('/api/models').then(setModelCatalog).catch(()=>{});
  const runtimeVersion=useRef(0);
  const [changingBrain,setChangingBrain]=useState(false);
  const loadModel=async()=>{const target=pidRef.current, version=++runtimeVersion.current;try{const r=await api(`/api/chats/${target}/runtime`);if(pidRef.current===target && version===runtimeVersion.current)setModel({pid:target,...r});}catch(e){toast(e.message);}};
  const changeRuntime=async(change)=>{
    if(changingBrain)return;
    const target=pidRef.current; ++runtimeVersion.current; setChangingBrain(true);
    try{await api(`/api/chats/${target}/runtime`,{method:'PUT',json:{backend:model.backend,...change},timeout:60000});if(pidRef.current===target){setChoices(null);await loadModel();warm(target);}}
    catch(e){toast(e.message);}finally{setChangingBrain(false);}
  };
  const [awake,setAwake]=useState(null);
  const [setup,setSetup]=useState(null);
  const [chats,setChats]=useState([]);
  const [newProject,setNewProject]=useState(false);
  const [projectDialog,setProjectDialog]=useState(null); // {kind:'rename'|'remove', project}
  const [connections,setConnections]=useState(false), [bigFile, setBigFile] = useState('');
  // Settings is a page with its own address: back (or Back to chat) returns to the chat.
  const openedHere = useRef(false);
  // Telegram linked (Ken in the cloud: the site knows; the Mac: its phone step), for the phone in the chat header.
  const [hostedPhone, setHostedPhone] = useState(null);
  const checkPhone = () => HOSTED && fetch('/account/telegram/status.json', { credentials: 'same-origin' }).then((r) => r.ok ? r.json() : Promise.reject())
    .then((r) => setHostedPhone(!!r.linked)).catch(() => {});
  useEffect(() => {
    if (!meta.hosted) return;  // known once Ken's meta is in
    checkPhone(); const back = () => { if (!location.hash.startsWith('#/settings')) checkPhone(); };
    window.addEventListener('hashchange', back); return () => window.removeEventListener('hashchange', back);
  }, [meta.hosted]);
  const phoneLinked = HOSTED ? hostedPhone : setup ? setup.phone === 'done' : null;
  const openSettings = (section = '') => { openedHere.current = true; location.hash = '#/settings' + (section ? '/' + section : ''); };
  const closeSettings = () => { if (openedHere.current) { openedHere.current = false; history.back(); } else location.hash = '#/'; };
  // Voice mode: only where the site Ken is hosted on offers it, with minutes left this month (/voice/status).
  const [talk, setTalk] = useState(null), [talking, setTalking] = useState(false);
  const checkTalk = () => voiceStatus().then(setTalk);
  const [projectMeta,setProjectMeta]=useState(null);
  const loadProjectMeta=(id)=>{ if(id==='home'){setProjectMeta(null);return;} api(`/api/agents/${id}`).then((v)=>setProjectMeta(v.agent)).catch(()=>setProjectMeta(null)); };
  const refreshChats=()=>api('/api/chats').then(setChats).catch(()=>{});
  // ⋯ and right-click on a project: a native menu in the Mac app.
  const projectMenu=async(e,id)=>{
    const native=window.kenDesktop; if(!native?.popupMenu)return;
    const project=chats.find((c)=>c.id===id); if(!project)return;
    const choice=await native.popupMenu([...[{id:'open',label:'Open in Finder'}],{id:'keys',label:'About, keys and tools…'},{id:'rename',label:'Rename…'},...[{id:'folder',label:'Code or info folder…'}],{separator:true},{id:'remove',label:'Remove from Ken…'}]);
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
  // A "Check for Updates…" the user asked for (Ken menu, menu-bar icon, Settings) answers with a
  // quiet toast; a downloaded update shows a bar with Restart now until it's installed.
  const [updateFailed,setUpdateFailed]=useState(null), [updateReady,setUpdateReady]=useState(null); const [restarting,setRestarting]=useState(false);
  useEffect(()=>{
    const native=window.kenDesktop; if(!native?.onUpdateState)return;
    native.updateFailed?.().then((s)=>{if(s)setUpdateFailed(s);}).catch(()=>{});
    native.updateStatus?.().then((s)=>{if(s?.ready)setUpdateReady(s.ready);}).catch(()=>{});
    return native.onUpdateState((s)=>{
      if(s.state==='channel'){setUpdateReady(null);setUpdateFailed(null);}
      if(s.state==='failed'){setUpdateFailed(s);setUpdateReady(null);}
      if(s.state==='ready')setUpdateReady(s.version);
      if(s.state==='restarting')setRestarting(true);
      if(s.state==='none')toast(`You’re up to date (${s.version}).`);
      if(s.state==='downloading')toast(`Ken ${s.version} is available. Downloading it now…`);
      if(s.state==='busy')toast('Ken is in the middle of something. It’ll restart into the update when it’s done.');
      if(s.state==='error')toast('Couldn’t check for updates. Try again later.');
    });
  },[]);
  // Whether an AI account is connected decides the first screen (Connect, or the chat), so nothing else shows until
  // it's known. If the check fails, the chat shows anyway and says what's wrong itself: never a stuck blank screen.
  const [setupFailed, setSetupFailed] = useState(false);
  const loadSetup=()=>api('/api/setup').then((r)=>{setSetup(r);setSetupFailed(false);}).catch(()=>{setSetupFailed(true);setTimeout(loadSetup,3000);});
  useEffect(()=>{loadSetup();},[]);
  // Poll while something is still getting ready (sign-in elsewhere, the voice model download).
  useEffect(()=>{if(!setup || (setup.connected && setup.voice.state!=='downloading'))return;const t=setInterval(loadSetup,2000);return()=>clearInterval(t);},[setup?.connected,setup?.voice?.state]);
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
  // ...and if it stays down, say so plainly with a way out instead of reconnecting forever.
  const [lost, setLost] = useState(false);
  const [reconnecting, setReconnecting] = useState(false), loadFailingSince = useRef(0);
  const ownAI = !!setup && Object.values(setup.providers || {}).some((p) => p.connected && p.how !== 'included');
  useEffect(() => { if (online) { setLost(false); return; } const t = setTimeout(() => setLost(true), 20000); return () => clearTimeout(t); }, [online]);
  const [loadError, setLoadError] = useState('');
  const pid = route.pid || 'home';
  const pidRef = useRef(pid); pidRef.current = pid;
  const warm = (target) => api(`/api/chats/${target}/warm`, {json:{}}).catch(()=>{});
  useEffect(()=>{warm(pid);},[pid]);
  const toast = useCallback((m) => setToastMsg(m), []);
  notify = toast;
  const [reporting, setReporting] = useState(false);
  openReport = () => setReporting(true);
  const [docFile, setDocFile] = useState(null);
  showDoc = setDocFile;
  useEffect(() => { if (!toastMsg) return; const t = setTimeout(() => setToastMsg(''), 6000 + 2000 * (String(toastMsg).split('\n').length - 1)); return () => clearTimeout(t); }, [toastMsg]);
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
        remember(conversation.messages); setChat({ pid: current, ...conversation }); setLoadError(''); setReconnecting(false); loadFailingSince.current = 0;
      }
    }).catch((e) => {
      if (pidRef.current !== current || generation.current !== version) return;
      // Ken's computer waking up or restarting into an update answers with errors for a little while: keep trying
      // quietly ("Reconnecting…") for about a minute before saying Ken can't be reached.
      if (Date.now() - (loadFailingSince.current ||= Date.now()) < 60000) { setReconnecting(true); setTimeout(() => load({ extras }), 3000); return; }
      setReconnecting(false); setLoadError(e.message);
    });
    api('/api/chats').then(setChats).catch(() => {});
    if(extras){api('/api/today').then(setToday).catch(() => {});api('/api/library').then(setLibrary).catch(() => {});}
  }, []);
  const loadMeta = () => api('/api/meta').then((m) => {
    HOSTED = !!m.hosted; HOSTED_BROWSER = !!m.browser; setMeta(m);
    if (HOSTED) document.documentElement.dataset.hosted = '';  // Ken in the cloud (the look is the same everywhere: theme.css)
    if (HOSTED && !window.kenDesktop) checkTalk();
    // In the cloud Ken's clock starts on UTC: use this browser's time zone until they say otherwise.
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (m.hosted && !m.timezone && zone) api('/api/command', { json: { text: '/timezone ' + zone } }).catch(() => {});
  }).catch(() => setTimeout(loadMeta, 3000));  // Ken waking or restarting: ask again until it answers (Settings waits on this)
  useEffect(() => {
    // #/settings/ai (the button in Ken's emails about AI credits), #/settings/apps, #/settings/phone: Settings, on that tab.
    const settingsLink = () => {
      const open = location.hash.match(/^#\/settings(?:\/([a-z-]+))?$/);
      setConnections(open ? (open[1] === 'ai' ? 'claude' : open[1] || 'start') : false);
      // #/send/<token>: a file too big for Telegram, sent here instead (SendBigFile).
      const big = location.hash.match(/^#\/send\/([0-9a-f]{24})$/);
      if (big) { history.replaceState(null, '', '#/'); setBigFile(big[1]); }
    };
    const onHash = () => { settingsLink(); setRoute(parseHash()); }; addEventListener('hashchange', onHash); settingsLink();
    if (window.kenDesktop) document.documentElement.dataset.desktop = window.kenDesktop.platform;
    loadMeta(); loadModel(); loadModelCatalog(); loadAwake();
    // In the cloud, Ken's computer stays awake while anything talks to it, and awake time counts against the plan:
    // a tab left in the background stops asking after a few minutes (see the events socket below).
    const refresh = setInterval(() => { if (!idleTab()) load(); }, 15000);
    const plan = setInterval(() => { if (!idleTab() && HOSTED) loadMeta(); }, 300000);
    return () => { removeEventListener('hashchange', onHash); clearInterval(refresh); clearInterval(plan); };
  }, []);
  useEffect(() => { setModel(null); loadModel(); load(); loadProjectMeta(pid); }, [pid]);
  useEffect(() => {
    let ws, timer, pending, closed = false, rev = null, retry = 0, resting = false, hiddenTimer, heard = Date.now();
    const busy = new Set();  // chats Ken is answering right now: their tab keeps listening
    const refresh = () => { clearTimeout(pending); pending = setTimeout(() => { load(); setTick((t) => t + 1); }, 100); };
    const connect = () => {
      ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/events'); heard = Date.now();
      ws.onopen = () => { setOnline(true); retry = 0; refresh(); };
      ws.onmessage = (e) => {
        heard = Date.now();
        const ev = JSON.parse(e.data), project = ev.project;
        if (ev.type === 'ping') return;
        if (ev.type === 'hello') { if (rev === null) rev = ev.rev; else if (ev.rev !== rev) location.reload(); return; }
        if (ev.type === 'ken.power') { setMeta((m) => ({ ...m, off: !ev.on })); return; }
        if (ev.type === 'coffee') { setAwake(ev.awake); window.kenDesktop?.coffeeChanged?.(); return; }
        if (ev.type === 'chat.runtime') { if(project===pidRef.current)loadModel(); return; }
        if (ev.type === 'chat.delta') { setStream((s) => ({ ...s, [project]: ev.text })); return; }
        if (ev.type === 'chat.tool') { setActivity((a) => ({ ...a, [project]: ev.text })); return; }
        if (ev.type === 'chat.busy') {
          generation.current++;
          if (ev.busy) busy.add(project); else busy.delete(project);
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
      ws.onclose = () => { setOnline(false); if (!closed && !resting) timer = setTimeout(connect, Math.min(8000, 500 * 2 ** retry++)); };
    };
    connect();
    // Ken pings every 20 seconds: a line that's gone quiet for 50 was cut on the way without a close, so start again.
    const watchdog = setInterval(() => { if (!closed && !resting && ws && ws.readyState === WebSocket.OPEN && Date.now() - heard > 50000) { const dead = ws; dead.onclose = null; dead.close(); setOnline(false); connect(); } }, 10000);
    // Ken in the cloud: hang up after 3 minutes in the background, or 5 untouched on screen (unless Ken is answering),
    // so its computer can sleep; pick up again, and catch up, the moment they're back.
    const rest = () => { if (resting) return; resting = true; clearTimeout(timer); if (ws) ws.close(); };
    const wake = () => { napping = false; if (resting && !document.hidden) { resting = false; retry = 0; connect(); loadMeta(); load(); } };
    const visibility = () => {
      if (!HOSTED) return;
      clearTimeout(hiddenTimer);
      if (document.hidden) hiddenTimer = setTimeout(rest, 180000);
      else { lastTouch = Date.now(); wake(); }
    };
    const touched = () => { lastTouch = Date.now(); if (napping) wake(); };
    const TOUCH = ['pointerdown', 'keydown', 'wheel', 'touchstart', 'focus'];
    let moved = 0;
    const mousemove = () => { const now = Date.now(); if (now - moved > 5000) { moved = now; touched(); } };
    TOUCH.forEach((t) => addEventListener(t, touched, { passive: true, capture: true }));
    addEventListener('mousemove', mousemove, { passive: true });
    const idleCheck = setInterval(() => {
      if (HOSTED && !napping && !busy.size && Date.now() - lastTouch > IDLE_MS) { napping = true; rest(); }
    }, 30000);
    document.addEventListener('visibilitychange', visibility);
    return () => { closed = true; clearInterval(watchdog); clearInterval(idleCheck); clearTimeout(timer); clearTimeout(pending); clearTimeout(hiddenTimer); document.removeEventListener('visibilitychange', visibility);
      TOUCH.forEach((t) => removeEventListener(t, touched, { capture: true })); removeEventListener('mousemove', mousemove); if (ws) ws.close(); };
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
    if(value==='/model' || value.startsWith('/model ')){
      if(!model)return true;
      const want=value.slice(6).trim(), match=model.choices.find(m=>m===want)||model.choices.find(m=>m.includes(want));
      if(!want){toast(`Model — current: ${model.current || 'default'}`);setChoices(model.choices.map(m=>({label:m,text:'/model '+m})));}
      else if(want==='default' || match)await changeRuntime({model:want==='default'?'':match});
      else toast(`No model matching “${want}”.`);
      return true;
    }
    if(value.startsWith('/')){
      const result=await api('/api/command',{json:{text:text.trim()},timeout:120000});
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
      const result = await api(`/api/chats/${pid}/reset`, { json: {} , timeout: 60000 });
      sessionStorage.removeItem('ken-draft:' + pid); setDraft(null); setChoices(null);
      for(const note of Object.values(voiceDrafts()))if(note.project===pid)saveVoiceDraft(note.id,null);
      setOutbox((all) => all.filter((m) => m.project !== pid));
      if (pidRef.current === pid) setChat({ pid, ...result });
      setStream((s) => ({ ...s, [pid]: '' })); setActivity((a) => ({ ...a, [pid]: '' }));
      toast(result.saving ? 'Cleared. Ken’s saving what mattered to memory.' : 'Cleared. Your memory and files are kept.');
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
  // Claude Code or Codex being downloaded (the Mac app gets them when first needed), or a failed download.
  const getting = model && setup?.runtimes?.[model.backend], brainLabel = model?.backend === 'codex' ? 'ChatGPT' : 'Claude';
  const brainConnected = !setup || (model && (setup.providers?.[model.backend]?.connected ?? setup.connected));
  const waiting = Object.values(setup?.runtimes || {}).some((x) => x.progress !== undefined) || visibleChat?.busy;
  useEffect(() => { if (!waiting) return; const t = setInterval(loadSetup, 1500); return () => clearInterval(t); }, [waiting]);
  const accountConnected = (r) => {setSetup(r);loadModel();loadModelCatalog();warm(pid);};
  return html`<div class="shell"><main class="main">${updateFailed && html`<div class="update-failed" role="alert">Ken couldn’t update itself to ${updateFailed.version}. <a href=${updateFailed.url} target="_blank" rel="noopener">Download the new version</a> and drag it into Applications over this one. Your chats and memory stay.</div>`}${updateReady && !updateFailed && html`<div class="update-ready" role="status">${restarting ? `Restarting into Ken ${updateReady}…` : html`Ken ${updateReady} is ready.<button class="btn primary" onClick=${()=>window.kenDesktop.installUpdate()}>Restart now</button>`}</div>`}<${Safe} where="header"><${ChatHeader} phone=${phoneLinked} onPhone=${() => openSettings('phone')} jobs=${pid === 'home' && today ? today.running.filter((r) => r.project === 'home') : []} onJobs=${() => { const r = today.running.find((x) => x.project === 'home'); if (r) setRunId(r.id); }} title=${pid !== 'home' && current ? current : null} brain=${route.brain} onSettings=${()=>openSettings()} onReport=${()=>setReporting(true)} onProjectMenu=${pid !== 'home' && window.kenDesktop?.popupMenu ? (e)=>projectMenu(e,pid) : null} connected=${brainConnected} awake=${awake} onAwake=${()=>api('/api/command',{json:{text:awake?'/decaf':'/coffee'}}).then((r)=>{setAwake(r.awake);toast(r.awake?'☕ Coffee’s on — lock your screen anytime; Ken keeps working.':'Decaf — this computer can sleep as usual.');}).catch((e)=>toast(e.message))} status=${restarting ? '' : loadError || lost ? 'Can’t reach Ken' : offline || reconnecting ? 'Reconnecting…' : getting ? (getting.error ? `Couldn’t download ${brainLabel}` : `Getting ${brainLabel} ready${getting.progress ? ` · ${Math.round(getting.progress * 100)}%` : '…'}`) : ''} onRetry=${restarting ? null : loadError ? load : lost ? () => location.reload() : getting?.error ? () => api('/api/setup', { json: { action: 'get-ready', backend: model.backend } }).then(setSetup).catch(() => {}) : null} onClear=${clearThread} clearing=${clearing} model=${model?.pid === pid ? model : null} changingBrain=${changingBrain || visibleChat?.busy} catalog=${modelCatalog} onRuntime=${changeRuntime}/><//>
    ${meta.usage?.lines?.some((l) => l.low && !(ownAI && /credit/i.test(l.label))) && html`<div class="update-ready plan-low" role="status">${meta.usage.note || `Your plan is running low: ${meta.usage.lines.filter((l) => l.low).map((l) => `${l.label.startsWith('Ken ') ? l.label : l.label.toLowerCase()}, ${l.left} left`).join('; ')}.`}${(meta.usage.manage_url || meta.account_url) && html` <a class="text-action" href=${meta.usage.manage_url || meta.account_url}>See options</a>`}</div>`}
    <${Safe} where="main">${meta.off ? html`<${KenOff} toast=${toast}/>` : !setup && !setupFailed ? html`<section class="conversation-body"><div class="starting"><img src="/ken.svg" alt=""/><p class="quiet-copy" role="status">Starting Ken…</p></div></section>` : setup && setup.ask_ai ? html`<${FirstThink} usage=${meta.usage} onPick=${(backend) => api('/api/chats/home/runtime', { method: 'PUT', json: { backend } }).then(loadModel).catch(() => {})} onConnected=${(r) => { accountConnected(r); load(); }} onCredits=${(r) => { setSetup(r); loadModel(); load(); }}/>` : setup && !setup.connected ? html`<${FirstConnect} onPick=${(backend) => api('/api/chats/home/runtime', { method: 'PUT', json: { backend } }).then(loadModel).catch(() => {})} onDone=${accountConnected}/>` : setup && model && !brainConnected ? html`<div class="brain-connect"><p class="np-help">Choose Claude or ChatGPT at the top. You can connect both in Settings.</p><${Connect} key=${model.backend} backend=${model.backend} onDone=${accountConnected}/></div>` : route.brain ? html`<${About} page pid="home" onPreview=${setPreview}/>` : html`<${ChatPage} seenTs=${opened.current.ts} voiceSetup=${setup?.voice} key=${pid + ':' + (chat && chat.pid === pid ? chat.cleared_at || 0 : 0)} pid=${pid} chat=${visibleChat} data=${today} files=${library.filter((f) => f.project === pid)} stream=${stream[pid]} activity=${activity[pid]} voice=${meta.voice && (!setup || setup.voice.state === 'ready')} toast=${toast} onPreview=${setPreview} draft=${pid === 'home' ? draft : null} onDraftUsed=${() => setDraft(null)} openRun=${setRunId} projects=${projects} onSend=${send} onVoice=${sendVoice} choices=${choices} onChoice=${(text)=>{setChoices(null);command(text).catch((e)=>toast(e.message));}} loadError=${loadError} onReload=${load} onAIAccounts=${()=>openSettings('claude')} onConnect=${(backend)=>openSettings('connect-' + backend)} usage=${meta.usage} onTalk=${pid === 'home' && talk?.available ? () => setTalking(true) : null}/>`}<//>
  </main>
    ${docFile && html`<${Safe} where="DocViewer"><${DocViewer} file=${docFile} onClose=${() => setDocFile(null)}/><//>`}
    ${runId && html`<${Safe} where="RunDrawer"><${RunDrawer} rid=${runId} projects=${projects} tick=${tick} onClose=${() => setRunId(null)} toast=${toast}/><//>`}
    ${preview && html`<${Safe} where="PreviewModal"><${PreviewModal} file=${preview} onClose=${() => setPreview(null)} toast=${toast}/><//>`}
    ${bigFile && html`<${Safe} where="SendBigFile"><${SendBigFile} key=${bigFile} token=${bigFile} onClose=${() => setBigFile('')}/><//>`}
    ${talking && html`<${Safe} where="VoiceMode"><${VoiceMode} onClose=${() => { setTalking(false); checkTalk(); }}/><//>`}
    ${connections && html`<${Safe} where="Settings"><${Settings} brain=${model?.backend} tab=${connections} onTab=${(t) => history.replaceState(null, '', '#/settings/' + t) || setConnections(t)} meta=${meta} setup=${setup} onConnected=${accountConnected} toast=${toast} onClose=${closeSettings} onAddTool=${(name)=>{location.hash='#/';send(`Add ${name} as a tool for you. Set it up whichever way works best (an MCP server, a command-line tool or its API), and ask me for anything you need, one thing at a time. Keys go in Keys, not in this chat.`,[],undefined,'home').catch((e)=>toast(e.message));}}/><//>`}
    ${projectDialog?.kind==='keys' && html`<${Safe} where="About"><${About} pid=${projectDialog.project.id} onPreview=${setPreview} onClose=${()=>setProjectDialog(null)}/><//>`}
    ${projectDialog?.kind==='rename' && html`<${Safe} where="RenameProject"><${RenameProject} project=${projectDialog.project} onClose=${()=>setProjectDialog(null)} onDone=${()=>{setProjectDialog(null);refreshChats();}}/><//>`}
    ${projectDialog?.kind==='remove' && html`<${Safe} where="RemoveProject"><${RemoveProject} project=${projectDialog.project} onClose=${()=>setProjectDialog(null)} onDone=${()=>{const gone=projectDialog.project.id;setProjectDialog(null);refreshChats();if(gone===pid)location.hash='#/';}}/><//>`}
    ${newProject && html`<${Safe} where="NewAgent"><${NewAgent} setup=${setup} onClose=${()=>setNewProject(false)} onCreated=${(p)=>{setNewProject(false);api('/api/chats').then(setChats).catch(()=>{});location.hash='#/p/'+p.id;}}/><//>`}
    ${reporting && html`<${Safe} where="ReportProblem"><${ReportProblem} onClose=${() => setReporting(false)}/><//>`}
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
