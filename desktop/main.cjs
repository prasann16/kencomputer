// Ken for Mac: a window onto the local `ken` service (bot.py). The service owns
// the engine, Telegram, voice and schedules; this app only shows its chat.
const { app, BrowserWindow, ipcMain, Menu, dialog, shell, session, Tray, nativeImage, systemPreferences } = require('electron');
const { execFile } = require('node:child_process');
const { readFileSync, existsSync, writeFileSync, mkdirSync, rmSync } = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { requestMicrophone } = require('./microphone.cjs');
const { trustedFrame } = require('./security.cjs');

app.setName('Ken');
const home = path.resolve(process.env.KEN_HOME || path.join(os.homedir(), '.ken'));
app.setPath('userData', path.join(home, 'desktop-data'));
let win, tray, quitting = false;

function service() {
  const env = existsSync(path.join(home, '.env')) ? readFileSync(path.join(home, '.env'), 'utf8') : '';
  const port = Number(process.env.KEN_WEB_PORT || (env.match(/^KEN_WEB_PORT=["']?(\d+)/m) || [])[1] || 7777);
  let token = '';
  try { token = readFileSync(path.join(home, 'web-token'), 'utf8').trim(); } catch {}
  return { origin: `http://127.0.0.1:${port}`, token };
}

// The running engine's version, or null if nothing answers.
async function running({ origin, token }) {
  if (!token) return null;
  try { const r = await fetch(origin + '/api/meta', { headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(1000) }); return r.ok ? (await r.json()).rev : null; }
  catch { return null; }
}

const run = (cmd, args) => new Promise((resolve) => execFile(cmd, args, { env: { ...process.env, KEN_HOME: home } }, () => resolve()));
// install.sh puts its own copy of Ken (and a venv) in the Ken home.
const terminalInstall = path.join(home, 'app', 'bot.py');
const appAgent = 'dev.kencomputer.app', appAgentFile = path.join(os.homedir(), 'Library/LaunchAgents', appAgent + '.plist');
const esc = (text) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;');

// Ken runs as a background service so Telegram and schedules work with the window closed.
// A terminal install (install.sh) keeps its own service; otherwise the app runs the engine it ships.
async function startService() {
  if (!app.isPackaged || existsSync(terminalInstall)) {
    const ken = [path.join(os.homedir(), '.local/bin/ken'), '/usr/local/bin/ken', '/opt/homebrew/bin/ken'].find(existsSync);
    if (!ken) throw new Error('Ken’s engine isn’t installed. Run this repo’s bot.py, or install Ken.');
    return run(ken, ['start']);
  }
  const engine = path.join(process.resourcesPath, 'engine', 'ken-engine');
  mkdirSync(path.dirname(appAgentFile), { recursive: true }); mkdirSync(path.join(home, 'logs'), { recursive: true });
  writeFileSync(appAgentFile, `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${appAgent}</string>
  <key>ProgramArguments</key><array><string>${esc(engine)}</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>KEN_HOME</key><string>${esc(home)}</string>
    <key>KEN_APP_VERSION</key><string>${app.getVersion()}</string>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${esc(path.join(home, 'logs/ken.log'))}</string>
  <key>StandardErrorPath</key><string>${esc(path.join(home, 'logs/ken.log'))}</string>
</dict></plist>
`);
  const domain = `gui/${process.getuid()}`;
  await run('/bin/launchctl', ['bootout', `${domain}/${appAgent}`]);
  await run('/bin/launchctl', ['bootstrap', domain, appAgentFile]);
}

// Connect to the running service, starting (or, after an app update, restarting) it if needed.
async function connect() {
  // When the app runs its own engine, the engine must be this app version.
  const want = app.isPackaged && !existsSync(terminalInstall) ? app.getVersion() : null;
  const ready = (rev) => rev !== null && (want === null || rev === want);
  if (ready(await running(service()))) return service();
  await startService();
  for (let i = 0; i < 120; i++) {
    if (ready(await running(service()))) return service();
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('Ken’s engine didn’t start. The log is in ~/.ken/logs/ken.log.');
}

// Updates download in the background and install while the window is closed and Ken
// isn't mid-task; the app then relaunches straight back into the menu bar.
// The chat shows one quiet "Update ready · Restart" pill once an update has downloaded;
// a check the user asked for (Ken menu, menu-bar icon) also reports "up to date".
const quietRelaunch = path.join(home, 'desktop-data', 'updated');
let updateReady = false, manualCheck = false;
const tellPage = (state) => { if (win && !win.isDestroyed()) win.webContents.send('update:state', state); };
async function engineBusy() {
  try {
    const { token, origin } = service();
    return (await (await fetch(origin + '/api/meta', { headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(2000) })).json()).busy;
  } catch { return false; }
}
function restartIntoUpdate(quiet) {
  if (quiet) writeFileSync(quietRelaunch, '');
  quitting = true;
  require('electron-updater').autoUpdater.quitAndInstall(true, true);
}
async function installIfIdle() {
  if (updateReady && !win?.isVisible() && !(await engineBusy())) restartIntoUpdate(true);
}
async function installNow() {
  if (!updateReady) return;
  if (await engineBusy()) { tellPage({ state: 'busy' }); return; }
  restartIntoUpdate(false);
}
function checkNow(manual) {
  if (!app.isPackaged) { if (manual) tellPage({ state: 'none', version: app.getVersion() }); return; }
  manualCheck = manual;
  require('electron-updater').autoUpdater.checkForUpdates().catch(() => {});
}
function checkForUpdates() {
  if (!app.isPackaged) return;
  const { autoUpdater } = require('electron-updater');
  autoUpdater.on('update-available', (info) => { if (manualCheck) tellPage({ state: 'downloading', version: info.version }); });
  autoUpdater.on('update-not-available', () => { if (manualCheck) tellPage({ state: 'none', version: app.getVersion() }); manualCheck = false; });
  autoUpdater.on('update-downloaded', (info) => { updateReady = true; manualCheck = false; tellPage({ state: 'ready', version: info.version }); installIfIdle(); });
  autoUpdater.on('error', (error) => { console.error('update check failed:', error.message); if (manualCheck) tellPage({ state: 'error' }); manualCheck = false; });
  checkNow(false);
  setInterval(() => checkNow(false), 4 * 60 * 60 * 1000);
  setInterval(installIfIdle, 10 * 60 * 1000);  // retry after a busy engine finishes
}

function external(url) {
  try { if (['https:', 'http:', 'mailto:'].includes(new URL(url).protocol)) shell.openExternal(url); } catch {}
}

function show() { if (win) { win.show(); win.focus(); } }

async function start() {
  // A background service can't run from a disk image or a quarantined download folder.
  if (app.isPackaged && !app.isInApplicationsFolder()) {
    const { response } = await dialog.showMessageBox({ message: 'Move Ken to your Applications folder?', detail: 'Ken runs in the background, so it needs to live in Applications.', buttons: ['Move to Applications', 'Quit'], defaultId: 0 });
    if (response !== 0 || !app.moveToApplicationsFolder()) { app.quit(); return; }
  }
  win = new BrowserWindow({ title: 'Ken', width: 900, height: 700, minWidth: 480, minHeight: 420, backgroundColor: '#ffffff', show: false,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default', trafficLightPosition: { x: 20, y: 21 },
    icon: path.join(__dirname, 'icon.png'), webPreferences: { preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
  win.on('close', (event) => { if (!quitting) { event.preventDefault(); win.hide(); } });
  win.on('hide', installIfIdle);
  const updated = existsSync(quietRelaunch);
  if (updated) rmSync(quietRelaunch);
  win.once('ready-to-show', () => { if (!updated) win.show(); });
  await win.loadFile(path.join(__dirname, 'loading.html'));

  const { origin, token } = await connect();
  await session.defaultSession.cookies.set({ url: origin, name: 'ken_token', value: token, httpOnly: true, sameSite: 'strict', path: '/' });
  // Only Ken's own page may use the microphone, and only for audio.
  const ours = (wc) => wc === win.webContents && new URL(wc.getURL()).origin === origin;
  session.defaultSession.setPermissionCheckHandler((wc, permission) => ours(wc) && permission === 'media');
  session.defaultSession.setPermissionRequestHandler((wc, permission, callback, details) =>
    callback(ours(wc) && permission === 'media' && (details.mediaTypes || []).every((type) => type === 'audio')));
  win.webContents.on('will-navigate', (event, url) => { if (new URL(url).origin !== origin) { event.preventDefault(); external(url); } });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(origin + '/api/chats/') && url.includes('/files/')) win.webContents.downloadURL(url);
    else external(url);
    return { action: 'deny' };
  });
  session.defaultSession.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({ title: 'Save from Ken', defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename())) });
  });
  ipcMain.handle('microphone:permission', (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    return requestMicrophone(systemPreferences, process.platform);
  });
  ipcMain.handle('update:action', async (event, action) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    if (action === 'check') checkNow(true);
    if (action === 'install') await installNow();
    if (action === 'state') return updateReady ? { state: 'ready' } : { state: 'idle', version: app.getVersion() };
  });
  ipcMain.handle('microphone:settings', async (event, kind) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    if (process.platform === 'darwin') await shell.openExternal(kind === 'privacy' ? 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone' : 'x-apple.systempreferences:com.apple.preference.sound?input');
  });
  await win.loadURL(origin);

  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'icon.png')).resize({ width: 20, height: 20 }));
  tray.setToolTip('Ken');
  tray.setContextMenu(Menu.buildFromTemplate([{ label: 'Open Ken', click: show }, { label: 'Check for Updates…', click: () => { show(); checkNow(true); } }, { type: 'separator' }, { label: 'Quit', click: () => app.quit() }]));
  tray.on('click', show);
  checkForUpdates();
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', show);
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      { label: app.name, submenu: [{ role: 'about' }, { label: 'Check for Updates…', click: () => { show(); checkNow(true); } }, { type: 'separator' }, { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' }, { type: 'separator' }, { role: 'quit' }] },
      { role: 'editMenu' },
      { label: 'View', submenu: [{ role: 'reload' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }, ...(!app.isPackaged ? [{ role: 'toggleDevTools' }] : [])] },
      { role: 'windowMenu' },
    ]));
    try { await start(); } catch (error) { dialog.showErrorBox('Could not open Ken', error.message); app.quit(); }
  });
  app.on('activate', show);
  // Quitting the app leaves the ken service (and Telegram) running.
  app.on('before-quit', () => { quitting = true; });
}
