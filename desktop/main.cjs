const { app, BrowserWindow, ipcMain, Menu, dialog, shell, session, Tray, nativeImage, systemPreferences } = require('electron');
const { spawn, execFile } = require('node:child_process');
const { readFileSync, existsSync, mkdirSync, appendFileSync } = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { requestMicrophone } = require('./microphone.cjs');
const { ClaudeAccount } = require('./account.cjs');
const { KenBrowser } = require('./browser.cjs');
const { trustedFrame } = require('./security.cjs');

app.setName('Ken');
// Separate app cookies and browser state when running tests or another Ken home.
const home = path.resolve(process.env.KEN_HOME || path.join(os.homedir(), '.ken'));
app.setPath('userData', path.join(home, 'desktop-data'));
let win, browser, account, engine, origin, token, tray, quitting = false;
const root = path.join(__dirname, '..');
const preload = path.join(__dirname, 'preload.cjs');
function log(text) { mkdirSync(path.join(home, 'logs'), { recursive: true }); appendFileSync(path.join(home, 'logs', 'desktop.log'), text); }
async function checkExistingService() {
  const tokenFile = path.join(home, 'web-token');
  if (!existsSync(tokenFile)) return;
  const envFile = path.join(home, '.env');
  const config = existsSync(envFile) ? readFileSync(envFile, 'utf8') : '';
  const port = Number(process.env.KEN_WEB_PORT || (config.match(/^KEN_WEB_PORT=["']?(\d+)/m) || [])[1] || 7777);
  let response;
  try { response = await fetch(`http://127.0.0.1:${port}/api/meta`, { headers: { Authorization: 'Bearer ' + readFileSync(tokenFile, 'utf8').trim() }, signal: AbortSignal.timeout(1000) }); } catch { return; }
  if (response.ok) throw new Error('Ken is already running as a background service. Stop it with “ken stop”, then reopen this app. Your conversations and memory will be kept.');
}
function launchEngine() {
  return new Promise((resolve, reject) => {
    let exe, args;
    if (app.isPackaged) {
      exe = path.join(process.resourcesPath, 'engine', process.platform === 'win32' ? 'ken-engine.exe' : 'ken-engine');
      args = ['--home', home];
    } else {
      const local = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
      exe = process.env.KEN_PYTHON || (existsSync(local) ? local : process.platform === 'win32' ? 'python' : 'python3');
      args = ['-m', 'desktop.runtime', '--home', home];
    }
    engine = spawn(exe, args, { cwd: app.isPackaged ? home : root, env: { ...process.env, PYTHONUNBUFFERED: '1', PATH: [process.env.PATH, path.join(os.homedir(), '.local/bin'), '/opt/homebrew/bin', '/usr/local/bin'].filter(Boolean).join(path.delimiter) }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let buffer = '', settled = false;
    const timer = setTimeout(() => { if (!settled) { settled = true; reject(new Error('Ken’s local engine took too long to start. Check ~/.ken/logs/desktop.log.')); } }, 45000);
    engine.stdout.on('data', (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split('\n'); buffer = lines.pop();
      for (const line of lines) {
        try { const message = JSON.parse(line); if (message.ready && Number.isInteger(message.port) && !settled) { settled = true; clearTimeout(timer); resolve('http://127.0.0.1:' + message.port); } } catch {}
      }
    });
    engine.stderr.on('data', (chunk) => log(chunk.toString()));
    engine.on('error', (err) => { clearTimeout(timer); if (!settled) { settled = true; reject(err); } });
    engine.on('exit', (code) => {
      clearTimeout(timer);
      if (!settled) { settled = true; reject(new Error('Ken’s local engine stopped during startup. See the desktop log for details.')); }
      else if (!quitting) { dialog.showErrorBox('Ken needs to restart', 'The local engine stopped. Quit and reopen Ken to reconnect. Your conversations are saved.'); }
    });
  });
}
async function api(route, body) {
  const response = await fetch(origin + route, { headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) }, method: body ? 'POST' : 'GET', body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(25000) });
  if (!response.ok) throw new Error('Ken could not connect to its local engine.');
  return response.json();
}
async function browserPump() {
  while (!quitting) {
    try {
      const command = await api('/api/browser/next');
      if (!command) continue;
      let result;
      try {
        if (!['navigate', 'read', 'click', 'fill', 'back', 'scroll'].includes(command.action)) throw new Error('Use navigate, read, click, fill, back, or scroll. Connection controls belong to the user.');
        result = await browser.action(command);
      } catch (error) { result = { error: error.message }; }
      await api('/api/browser/results/' + command.id, result);
    } catch (error) { if (!quitting) await new Promise((resolve) => setTimeout(resolve, 1500)); }
  }
}
function external(url) {
  try { const parsed = new URL(url); if (['https:', 'http:', 'mailto:'].includes(parsed.protocol)) shell.openExternal(url); } catch {}
}
async function start() {
  mkdirSync(home, { recursive: true });
  win = new BrowserWindow({ title: 'Ken', width: 900, height: 700, minWidth: 600, minHeight: 480, backgroundColor: '#ffffff', show: false,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default', trafficLightPosition: { x: 20, y: 21 },
    icon: path.join(__dirname, 'icon.png'), webPreferences: { preload, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true } });
  win.on('close', (event) => { if (!quitting) { event.preventDefault(); win.hide(); } });
  win.once('ready-to-show', () => win.show());
  await win.loadFile(path.join(__dirname, 'loading.html'));
  // A test harness may supply a loopback fixture server; packaged apps always own their engine.
  if (!app.isPackaged && process.env.KEN_ENGINE_URL) {
    const url = new URL(process.env.KEN_ENGINE_URL);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.pathname !== '/') throw new Error('The development engine must be on 127.0.0.1.');
    origin = url.origin;
  } else { await checkExistingService(); origin = await launchEngine(); }
  token = readFileSync(path.join(home, 'web-token'), 'utf8').trim();
  await session.defaultSession.cookies.set({ url: origin, name: 'ken_token', value: token, httpOnly: true, sameSite: 'strict', path: '/' });
  session.defaultSession.setPermissionCheckHandler((wc, permission, requestingOrigin) => wc === win.webContents && requestingOrigin === origin && permission === 'media');
  session.defaultSession.setPermissionRequestHandler(async (wc, permission, callback, details) => {
    if (wc !== win.webContents || new URL(wc.getURL()).origin !== origin || permission !== 'media' || (details.mediaTypes || []).some((type) => type !== 'audio')) { callback(false); return; }
    // The mic button is the explicit action; macOS owns the persistent OS permission.
    callback(true);
  });
  win.webContents.on('will-navigate', (event, url) => { const target = new URL(url); if (target.origin !== origin || target.pathname !== '/') { event.preventDefault(); external(url); } });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(origin + '/api/chats/') && url.includes('/files/')) win.webContents.downloadURL(url);
    else external(url);
    return { action: 'deny' };
  });
  session.defaultSession.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({ title: 'Save from Ken', defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename())) });
  });
  const openChrome = (url) => new Promise((resolve, reject) => {
    let exe, args;
    if (process.platform === 'darwin') { exe = 'open'; args = ['-a', 'Google Chrome', ...(url ? [url] : [])]; }
    else if (process.platform === 'win32') {
      exe = [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA].filter(Boolean).map(base => path.join(base, 'Google/Chrome/Application/chrome.exe')).find(existsSync);
      if (!exe) { reject(new Error('Install Google Chrome to connect your browser.')); return; }
      args = url ? [url] : [];
    } else { exe = 'google-chrome'; args = url ? [url] : []; }
    execFile(exe, args, (error) => error ? reject(new Error('Could not open Google Chrome. Check that it is installed.')) : resolve());
  });
  browser = new KenBrowser(win, { openChrome, ...(!app.isPackaged && process.env.KEN_TEST_CDP ? { endpoint: process.env.KEN_TEST_CDP } : {}) });
  ipcMain.handle('browser:action', (event, action) => { if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.'); return browser.action(action); });
  const claude = app.isPackaged
    ? path.join(process.resourcesPath,'engine','_internal','claude_agent_sdk','_bundled',process.platform==='win32'?'claude.exe':'claude')
    : path.join(root,'.venv',process.platform==='win32'?'Lib/site-packages':'lib/python3.11/site-packages','claude_agent_sdk','_bundled',process.platform==='win32'?'claude.exe':'claude');
  account=new ClaudeAccount(claude,url=>shell.openExternal(url));
  ipcMain.handle('account:action',async(event,action)=>{
    if(!trustedFrame(event,win,origin))throw new Error('Untrusted request.');
    if(action==='status')return account.status();
    if(action==='connect')return account.connect();
    throw new Error('Unknown account action.');
  });
  ipcMain.handle('microphone:permission',async(event)=>{
    if(!trustedFrame(event,win,origin))throw new Error('Untrusted request.');
    return requestMicrophone(systemPreferences,process.platform);
  });
  ipcMain.handle('microphone:settings',async(event,kind)=>{
    if(!trustedFrame(event,win,origin))throw new Error('Untrusted request.');
    if(process.platform==='darwin')await shell.openExternal(kind==='privacy'?'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone':'x-apple.systempreferences:com.apple.preference.sound?input');
    else if(process.platform==='win32')await shell.openExternal('ms-settings:privacy-microphone');
  });
  await win.loadURL(origin);
  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'icon.png')).resize({ width: 20, height: 20 }));
  tray.setToolTip('Ken');
  tray.setContextMenu(Menu.buildFromTemplate([{ label: 'Open Ken', click: () => { win.show(); win.focus(); } }, { type: 'separator' }, { label: 'Quit Ken', click: () => app.quit() }]));
  tray.on('click', () => { win.show(); win.focus(); });
  browserPump();
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { win.show(); win.focus(); } });
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      ...(process.platform === 'darwin' ? [{ label: 'Ken', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'hide' }, { role: 'hideOthers' }, { type: 'separator' }, { role: 'quit' }] }] : []),
      { label: 'File', submenu: [{ label: 'Show Ken', accelerator: 'CmdOrCtrl+1', click: () => { win.show(); win.focus(); } }, { role: 'quit' }] },
      { label: 'Edit', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
      { label: 'View', submenu: [{ role: 'reload' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }, ...(!app.isPackaged ? [{ role: 'toggleDevTools' }] : [])] },
      { label: 'Window', submenu: [{ role: 'minimize' }, { role: 'close' }] }
    ]));
    try { await start(); } catch (error) { dialog.showErrorBox('Could not open Ken', error.message); app.quit(); }
  });
  app.on('activate', () => { if (win) win.show(); });
  app.on('before-quit', () => { quitting = true; if (account) account.close(); if (browser) browser.close(); if (engine) engine.kill(); });
}
