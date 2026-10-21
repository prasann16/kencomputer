import { html, useState, useEffect } from '/vendor/preact-htm.js';

export function CloudflareGuide({ api, service, initialUrl = '' }) {
  const [status, setStatus] = useState(null), [checking, setChecking] = useState(false), [error, setError] = useState('');
  const [url, setUrl] = useState(initialUrl), [accessId, setAccessId] = useState(''), [accessSecret, setAccessSecret] = useState('');
  const [testing, setTesting] = useState(false), [verified, setVerified] = useState(''), [copied, setCopied] = useState('');
  const [localOnly, setLocalOnly] = useState(false);
  const [installing, setInstalling] = useState(false);
  const refresh = async () => {
    setChecking(true); setVerified('');
    try { setStatus(await api('/cloudflare')); setError(''); }
    catch (e) { setError(e.message); }
    finally { setChecking(false); }
  };
  useEffect(() => { refresh(); const focus = () => refresh(); window.addEventListener('focus', focus); return () => window.removeEventListener('focus', focus); }, []);
  useEffect(() => {
    if (status?.install.state !== 'running') return;
    const timer = setTimeout(refresh, 2500);
    return () => clearTimeout(timer);
  }, [status]);
  const install = async () => {
    setInstalling(true); setError('');
    try { await api('/cloudflare/install', {}); await refresh(); }
    catch (e) { setError(e.message); }
    finally { setInstalling(false); }
  };
  const copy = async (text, label) => {
    try { await navigator.clipboard.writeText(text); setCopied(label); }
    catch { setError('Couldn’t copy. Select the text and copy it manually.'); }
  };
  const test = async (address = url) => {
    setTesting(true); setError(''); setVerified(''); setCopied('');
    try { const r = await api('/cloudflare/verify', { url: address, access_id: accessId, access_secret: accessSecret }); setUrl(r.url); setVerified(r.url); setLocalOnly(r.local_only); }
    catch (e) { setError(e.message); }
    finally { setTesting(false); }
  };
  const change = (setter) => (e) => { setter(e.target.value); setVerified(''); setCopied(''); };
  const runningInstall = installing || status?.install.state === 'running';
  return html`<div class="cloudflare-guide">
    <div class="cloudflare-status" role="status">
      <span><b>${!status ? 'Checking this Mac…' : runningInstall ? 'Installing Cloudflare…' : status.installed ? 'Cloudflare is installed' : status.running ? 'Cloudflare is running' : 'Cloudflare wasn’t found'}</b>
      <small>${status && (runningInstall ? 'This can take a few minutes.' : status.running ? 'A cloudflared process is running on this Mac.' : status.running === null ? 'Ken couldn’t check whether a tunnel is running.' : status.service_installed ? 'A background service is installed, but no process is running.' : status.installed ? 'No tunnel process is running yet.' : 'Install cloudflared to connect this Mac.')}</small></span>
      <button type="button" class="text-action" disabled=${checking || runningInstall} onClick=${refresh}>${checking && status ? 'Checking…' : 'Check again'}</button>
    </div>
    ${status?.config_unreadable && html`<p>Some local settings couldn’t be read. You can still enter your tunnel address below.</p>`}
    ${status && !status.installed && !status.running && html`<div class="cloudflare-install">
      ${status.brew && status.platform === 'Darwin' ? html`<p>Homebrew is available. Ken can use it to install cloudflared on this Mac.</p><button type="button" class="btn" disabled=${runningInstall} onClick=${install}>${runningInstall ? 'Installing…' : 'Install with Homebrew'}</button>` : html`<p>Install Homebrew, then come back and choose Check again.</p><a class="text-action" href="https://brew.sh" target="_blank" rel="noopener">Install Homebrew ↗</a>`}
      <a class="text-action" href="https://developers.cloudflare.com/tunnel/downloads/#macos" target="_blank" rel="noopener">Other ways to install ↗</a>
    </div>`}
    ${status?.install.state === 'error' && html`<p class="connect-error" role="alert">${status.install.error}</p><code>brew install cloudflared</code><button type="button" class="text-action" onClick=${() => copy('brew install cloudflared', 'install')}>${copied === 'install' ? 'Copied' : 'Copy command'}</button>`}
    ${status?.routes.length > 0 && html`<div class="cloudflare-found"><p>Found in this Mac’s tunnel settings:</p>${status.routes.map((address) => html`<div class="cloudflare-found-row" key=${address}><span>${address}</span><button type="button" class="text-action" disabled=${testing} onClick=${() => { setUrl(address); test(address); }}>Use this address</button></div>`)}</div>`}
    <details class="cloudflare-instructions" open=${!!status && !status.routes.length && !initialUrl}>
      <summary>${status?.running || status?.service_installed || status?.config_found ? 'Use your existing tunnel' : 'Set up a tunnel'}</summary>
      <p>You’ll need your Cloudflare account and a domain on Cloudflare.</p>
      <ol>
        <li>Open Cloudflare → Networking → Tunnels. Choose the tunnel running on this Mac, or create one.</li>
        <li>${status?.running ? 'Keep the existing connector. There’s no need to install another one.' : status?.service_installed ? html`<span>Start your existing connector and wait for Healthy in Cloudflare. <a href="https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/macos/" target="_blank" rel="noopener">Start the macOS service ↗</a></span>` : 'For a new tunnel, select macOS and run its Install and Run command in Terminal on this Mac. Wait for Healthy.'}</li>
        <li>Add a published application route with a hostname such as ken.example.com. Use this HTTP service address:<code>${service}</code><button type="button" class="text-action" onClick=${() => copy(service, 'service')}>${copied === 'service' ? 'Copied' : 'Copy service address'}</button></li>
        <li>Save the route, then enter its HTTPS address below. Leave the path empty so chat and live updates both work.</li>
      </ol>
      <div class="cloudflare-links"><a class="text-action" href="https://dash.cloudflare.com" target="_blank" rel="noopener">Open Cloudflare ↗</a><a class="text-action" href="https://developers.cloudflare.com/tunnel/get-started/" target="_blank" rel="noopener">Setup guide ↗</a></div>
      ${status?.config_found && html`<p>Your Mac also has a local tunnel configuration. If you manage routes in that file, add Ken there and restart your existing service. <a href="https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/" target="_blank" rel="noopener">Local configuration guide ↗</a></p>`}
      ${status && !status.service_installed && html`<p>For work to continue after Terminal closes, run the tunnel as a background service. <a href="https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/macos/" target="_blank" rel="noopener">macOS service guide ↗</a></p>`}
    </details>
    <form class="cloudflare-test" onSubmit=${(e) => { e.preventDefault(); test(); }}>
      <label class="np-label">Tunnel address<input class="np-input" type="url" required value=${url} placeholder="https://ken.example.com" spellcheck="false" autoCapitalize="none" disabled=${testing} onInput=${change(setUrl)}/></label>
      <details class="computer-access"><summary>My tunnel uses Cloudflare Access</summary><p>Enter a service token allowed by a Service Auth policy. This check won’t save it.</p><label class="np-label">Client ID<input class="np-input" value=${accessId} autoCapitalize="none" spellcheck="false" disabled=${testing} onInput=${change(setAccessId)}/></label><label class="np-label">Client secret<input class="np-input" type="password" value=${accessSecret} autocomplete="off" disabled=${testing} onInput=${change(setAccessSecret)}/></label></details>
      <button type="submit" class="btn" disabled=${testing || !url.trim()}>${testing ? 'Testing…' : 'Test connection'}</button>
    </form>
    ${verified && (localOnly ? html`<p role="status">Ken works locally, but this address only works on this Mac. Enter your Cloudflare HTTPS address to check the tunnel.</p>` : html`<div class="cloudflare-verified" role="status"><b>Connected to Ken on this Mac.</b><p>Ken is reachable and live updates are connected. Use this address with the server key below on your client Mac.</p><code>${verified}</code><button type="button" class="text-action" onClick=${() => copy(verified, 'address')}>${copied === 'address' ? 'Copied' : 'Copy address'}</button></div>`)}
    ${error && html`<p class="connect-error" role="alert">${error}</p>`}
  </div>`;
}
