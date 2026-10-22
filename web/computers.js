import { html, useState, useEffect } from '/vendor/preact-htm.js';
import { CloudflareGuide } from '/cloudflare.js';

export const computerId = (location.pathname.match(/^\/computer\/([a-f0-9]{16})$/) || [])[1] || 'local';
export const isRemote = computerId !== 'local';
export const computerUrl = (path) => isRemote ? `/remote/${computerId}${path}` : path;
export const computerStorage = (key) => isRemote ? `${key}:${computerId}` : key;

const monitor = html`<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>`;
const laptop = html`<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="4" width="14" height="12" rx="2"/><path d="m5 16-3 4h20l-3-4M9 20h6"/></svg>`;

async function localApi(path, json, method) {
  const r = await fetch('/api/computers' + path, {
    method: method || (json === undefined ? 'GET' : 'POST'),
    headers: json === undefined ? {} : { 'Content-Type': 'application/json' },
    body: json === undefined ? undefined : JSON.stringify(json),
    signal: AbortSignal.timeout(16000),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || 'Couldn’t update your computers. Try again.');
  return data;
}

async function choose(id) {
  const r = await localApi('/select', { id });
  location.assign(r.path);
}

export function ComputerBar({ Modal, online }) {
  const [open, setOpen] = useState(false), [name, setName] = useState(isRemote ? 'Remote computer' : 'This Mac'), [saved, setSaved] = useState(0);
  useEffect(() => { localApi('').then((s) => { setSaved(s.computers.length); setName(isRemote ? s.computers.find((c) => c.id === computerId)?.name || 'Removed computer' : 'This Mac'); }).catch(() => {}); }, []);
  // Most people have one Mac: the bar shows up only once another computer is saved (Settings → Computers).
  if (!isRemote && !saved) return null;
  return html`<div class="computer-bar">
    <button class="computer-switch" onClick=${() => setOpen(true)} aria-label=${`Choose computer, current: ${name}`} aria-haspopup="dialog">
      ${isRemote ? monitor : laptop}<span>${name}</span><i class=${'computer-dot ' + (online ? 'online' : '')}></i><svg class="computer-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3 4.5 3 3 3-3"/></svg>
    </button>
    ${isRemote && html`<span class="computer-caption">${online ? 'Keeps running when this Mac is offline.' : 'Reconnecting. Accepted work stays on this computer.'}</span>`}
    ${open && html`<${Modal} title="Your computers" className="computer-modal" closeX onClose=${() => setOpen(false)}><${ComputerSettings}/><//>`}
  </div>`;
}

export function ComputerSettings({ server = false }) {
  const [state, setState] = useState(null), [form, setForm] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [checks, setChecks] = useState({}), [remove, setRemove] = useState(null);
  const load = () => localApi('').then((s) => {
    setState(s);
    for (const c of s.computers) localApi(`/${c.id}/check`, {}).then((r) => setChecks((old) => ({ ...old, [c.id]: r }))).catch(() => setChecks((old) => ({ ...old, [c.id]: { online: false } })));
  }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const act = async (fn) => { setError(''); setBusy(true); try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  if (form) return html`<${ConnectComputer} onBack=${() => { setForm(false); load(); }}/>`;
  return html`<div class="computers-panel">
    <p class="computer-intro">Choose the computer Ken works on.</p>
    <div class="computer-list" aria-label="Computers">
      <div class=${'computer-row ' + (!isRemote ? 'selected' : '')}><span class="computer-icon">${laptop}</span><div class="computer-row-text"><b>This Mac</b><small>${state?.local_name || 'Local computer'}</small></div>${!isRemote ? html`<span class="computer-current">In use</span>` : html`<button class="btn ghost" disabled=${busy} onClick=${() => act(() => choose('local'))}>Use this Mac</button>`}</div>
      ${state?.computers.map((c) => html`<div class=${'computer-saved ' + (c.id === computerId ? 'selected' : '')} key=${c.id}>
        <div class="computer-row"><span class="computer-icon">${monitor}</span><div class="computer-row-text"><b>${c.name}</b><small><i class=${'computer-dot ' + (checks[c.id]?.online ? 'online' : '')}></i>${checks[c.id] ? checks[c.id].online ? 'Available' : 'Offline' : 'Checking…'} · ${new URL(c.url).host}</small></div>${c.id === computerId ? html`<span class="computer-current">In use</span>` : html`<button class="btn ghost" disabled=${busy} onClick=${() => act(() => choose(c.id))}>Open</button>`}</div>
        <div class="computer-row-actions"><button class="text-action" disabled=${busy} onClick=${() => act(async () => { const r = await localApi(`/${c.id}/check`, {}); setChecks((old) => ({ ...old, [c.id]: r })); if (!r.online) setError(r.error); })}>Check connection</button><button class="text-action" disabled=${busy} onClick=${() => setRemove(c.id)}>Remove</button></div>
        ${remove === c.id && html`<div class="computer-remove"><p>Remove this connection? Work on ${c.name} will keep running.</p><button class="btn ghost" onClick=${() => setRemove(null)}>Keep</button><button class="btn ghost" disabled=${busy} onClick=${() => act(async () => { await localApi('/' + c.id, undefined, 'DELETE'); if (c.id === computerId) await choose('local'); else { setRemove(null); load(); } })}>Remove connection</button></div>`}
      </div>`)}
    </div>
    ${!state && !error && html`<p class="np-note" role="status">Loading your computers…</p>`}
    <button class="connect-computer-button" onClick=${() => setForm(true)}><span aria-hidden="true">＋</span> Connect a computer</button>
    <p class="computer-footnote">Chats and files stay on each computer. Switching won’t stop its tasks.</p>
    ${error && html`<p class="connect-error" role="alert">${error} ${!state && html`<button class="text-action" onClick=${load}>Retry</button>`}</p>`}
    ${server ? html`<${ServerSettings}/>` : html`<p class="computer-server-hint">Server setup is in Settings → Computers on the other Mac.</p>`}
  </div>`;
}

function ConnectComputer({ onBack }) {
  const [name, setName] = useState(''), [url, setUrl] = useState(''), [key, setKey] = useState('');
  const [accessId, setAccessId] = useState(''), [accessSecret, setAccessSecret] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setError('');
    try { const c = await localApi('', { name, url, key, access_id: accessId, access_secret: accessSecret }); setKey(''); setAccessSecret(''); await choose(c.id); }
    catch (e) { setError(e.name === 'TimeoutError' ? 'The connection timed out. Check your server and tunnel, then try again.' : e.message); setBusy(false); }
  };
  return html`<form class="computer-connect" onSubmit=${submit}>
    <button type="button" class="text-action computer-back" onClick=${onBack}>← Your computers</button>
    <div class="computer-connect-intro"><h3>Connect a computer</h3><p>Use the address and key from Ken on your other Mac.</p></div>
    <label class="np-label">Computer name <span class="np-optional">optional</span><input class="np-input" placeholder="Mac mini at home" value=${name} maxLength="80" onInput=${(e) => setName(e.target.value)}/></label>
    <label class="np-label">Server address<input class="np-input" type="url" required placeholder="https://ken.example.com" value=${url} autoCapitalize="none" spellcheck="false" onInput=${(e) => setUrl(e.target.value)}/><small>Your Cloudflare Tunnel’s HTTPS address.</small></label>
    <label class="np-label">Server key<input class="np-input" type="password" required placeholder="Paste the key from your other Mac" value=${key} autocomplete="off" onInput=${(e) => setKey(e.target.value)}/><small>On that Mac: Settings → Computers → Use this Mac as a server.</small></label>
    <details class="computer-access"><summary>My tunnel uses Cloudflare Access</summary><p>Use a service token allowed by a Service Auth policy.</p><label class="np-label">Client ID<input class="np-input" value=${accessId} autoCapitalize="none" spellcheck="false" onInput=${(e) => setAccessId(e.target.value)}/></label><label class="np-label">Client secret<input class="np-input" type="password" value=${accessSecret} autocomplete="off" onInput=${(e) => setAccessSecret(e.target.value)}/></label></details>
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
    <button type="submit" class="btn primary computer-submit" disabled=${busy || !url.trim() || !key.trim()}>${busy ? 'Connecting…' : 'Connect'}</button>
    <p class="computer-footnote">Your key stays on this Mac. Files you attach are sent to the computer you choose.</p>
  </form>`;
}

function ServerSettings() {
  const [open, setOpen] = useState(false), [state, setState] = useState(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [copied, setCopied] = useState(false), [rotate, setRotate] = useState(false), [awake, setAwake] = useState(false);
  useEffect(() => { if (open) localApi('/server').then(setState).catch((e) => setError(e.message)); }, [open]);
  const update = async (change) => { setBusy(true); setError(''); try { setState(await localApi('/server', change, 'PUT')); setRotate(false); setCopied(false); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  return html`<details class="computer-server" onToggle=${(e) => setOpen(e.currentTarget.open)}><summary>Use this Mac as a server <span>Optional</span></summary>
    <div class="server-content"><p>Let your other Macs connect to Ken here. Chats and jobs keep running when the client disconnects.</p>
    ${state && html`<div class="server-toggle"><span>Allow remote connections<small>${state.enabled ? 'Enabled on this Mac' : 'Only this Mac can connect'}</small></span><input class="job-switch" type="checkbox" role="switch" aria-label="Allow remote connections" disabled=${busy} checked=${state.enabled} onChange=${(e) => update({ enabled: e.target.checked })}/></div>
    ${state.enabled && html`<ol class="server-steps"><li><b>Set up Cloudflare</b><${CloudflareGuide} key=${state.key} api=${localApi} service=${state.service} initialUrl=${state.url || ''}/></li>
    <li><b>Connect from your other Mac</b><p>Use your HTTPS hostname and this server key in Ken’s “Connect a computer” screen.</p><div class="server-key"><input type="password" class="np-input" aria-label="Server key" readonly value=${state.key}/><button class="btn ghost" onClick=${async () => { try { await navigator.clipboard.writeText(state.key); setCopied(true); } catch { setError('Couldn’t copy. Select the key and copy it manually.'); } }}>${copied ? 'Copied' : 'Copy key'}</button></div><small class="computer-footnote">This key grants access to Ken’s files and tools. Share it only with your own clients.</small></li>
    <li><b>Leave this Mac ready to work</b><p>Keep it powered, awake, and signed in. The installed Ken app runs its engine in the background after you quit.</p><button class="text-action" onClick=${async () => { try { const r = await fetch('/api/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: '/coffee' }) }); const data = await r.json(); if (!r.ok || !data.awake) throw new Error(data.error || data.reply || 'Enable Keep Awake in Ken on this Mac.'); setAwake(true); } catch (e) { setError(e.message); } }}>${awake ? 'Keep Awake is on' : 'Turn on Keep Awake'}</button></li></ol>
    <div class="server-rotate">${rotate ? html`<p>Replace this key? Existing clients will need the new one.</p><button class="text-action" onClick=${() => setRotate(false)}>Cancel</button><button class="text-action" disabled=${busy} onClick=${() => update({ rotate: true })}>Replace key</button>` : html`<button class="text-action" onClick=${() => setRotate(true)}>Replace server key…</button>`}</div>`}`}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}</div>
  </details>`;
}
