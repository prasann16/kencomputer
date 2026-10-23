// Ken for Mac: a window onto the local `ken` service (bot.py). The service owns
// the engine, Telegram, voice and schedules; this app only shows its chat.
const { app, BrowserWindow, ipcMain, Menu, dialog, shell, session, Tray, nativeImage, nativeTheme, systemPreferences, clipboard } = require('electron');
const { execFile } = require('node:child_process');
const { readFileSync, existsSync, writeFileSync, mkdirSync, rmSync, statSync } = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { requestMicrophone } = require('./microphone.cjs');
const { trustedFrame } = require('./security.cjs');
const { UpdateChecks, acceptsVersion } = require('./updates.cjs');

app.setName('Ken');
const home = path.resolve(process.env.KEN_HOME || path.join(os.homedir(), '.ken'));
app.setPath('userData', path.join(home, 'desktop-data'));
let win, tray, quitting = false;
const themeFile = path.join(home, 'desktop-data', 'theme');
const isTheme = (value) => ['system', 'light', 'dark'].includes(value);
const windowBackground = () => nativeTheme.shouldUseDarkColors ? '#181818' : '#ffffff';

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
// The app always runs the engine it ships (so updates reach it). An older terminal install
// (install.sh) shares the Ken home, so its service is switched off rather than left running old code.
const terminalAgent = 'dev.kencomputer.ken';
async function startService() {
  if (!app.isPackaged) {
    const ken = [path.join(os.homedir(), '.local/bin/ken'), '/usr/local/bin/ken', '/opt/homebrew/bin/ken'].find(existsSync);
    if (!ken) throw new Error('Ken’s engine isn’t installed. Run this repo’s bot.py, or install Ken.');
    return run(ken, ['start']);
  }
  const engine = path.join(process.resourcesPath, 'engine', 'ken-engine');
  // The browser tool runs on the helper's Node: macOS keeps the helper out of the Dock, while
  // the app's own binary run as Node shows up there as a second Ken that bounces forever.
  const node = path.join(process.resourcesPath, '..', 'Frameworks', 'Ken Helper.app', 'Contents', 'MacOS', 'Ken Helper');
  mkdirSync(path.dirname(appAgentFile), { recursive: true }); mkdirSync(path.join(home, 'logs'), { recursive: true });
  writeFileSync(appAgentFile, `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${appAgent}</string>
  <key>ProgramArguments</key><array><string>${esc(engine)}</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>KEN_HOME</key><string>${esc(home)}</string>
    <key>KEN_APP_VERSION</key><string>${app.getVersion()}</string>
    <key>KEN_NODE</key><string>${esc(node)}</string>
    <key>KEN_BROWSER_MCP</key><string>${esc(path.join(process.resourcesPath, 'browser', 'chrome-devtools-mcp', 'build', 'src', 'bin', 'chrome-devtools-mcp.js'))}</string>
    <key>PATH</key><string>${esc(path.join(process.resourcesPath, 'ffmpeg'))}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${esc(path.join(home, 'logs/ken.log'))}</string>
  <key>StandardErrorPath</key><string>${esc(path.join(home, 'logs/ken.log'))}</string>
</dict></plist>
`);
  const domain = `gui/${process.getuid()}`;
  if (existsSync(terminalInstall)) {
    await run('/bin/launchctl', ['bootout', `${domain}/${terminalAgent}`]);
    await run('/bin/launchctl', ['disable', `${domain}/${terminalAgent}`]);
  }
  await run('/bin/launchctl', ['bootout', `${domain}/${appAgent}`]);
  await stopOtherEngine();
  await run('/bin/launchctl', ['bootstrap', domain, appAgentFile]);
}

// Only one engine runs per Ken home (engine.lock holds its pid). One the app didn't
// start, like a copy run by hand, is stopped so the app's own engine can take over.
async function stopOtherEngine() {
  let pid = 0;
  try { pid = Number(readFileSync(path.join(home, 'engine.lock'), 'utf8').trim()); } catch {}
  if (!pid || pid === process.pid) return;
  const command = await new Promise((resolve) => execFile('/bin/ps', ['-p', String(pid), '-o', 'command='], (_e, out) => resolve(String(out || ''))));
  if (!/ken-engine|bot\.py/.test(command)) return;  // gone, or the pid now belongs to something else
  try { process.kill(pid, 'SIGTERM'); } catch {}
  for (let i = 0; i < 20; i++) {
    try { process.kill(pid, 0); } catch { return; }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  try { process.kill(pid, 'SIGKILL'); } catch {}
}

// Connect to the running service, starting (or, after an app update, restarting) it if needed.
async function connect() {
  // When the app runs its own engine, the engine must be this app version.
  const want = app.isPackaged ? app.getVersion() : null;
  const ready = (rev) => rev !== null && (want === null || rev === want);
  if (ready(await running(service()))) return service();
  await startService();
  for (let i = 0; i < 120; i++) {
    if (ready(await running(service()))) return service();
    if (i === 40) await startService();  // nothing after 20 seconds: start it over once
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('Ken’s engine didn’t start. The log is in ~/.ken/logs/ken.log.');
}

// Connect, or tell the user plainly and let them try again. Null if they chose Quit.
async function connectOrAsk() {
  for (;;) {
    try { return await connect(); } catch (error) {
      const { response } = await dialog.showMessageBox({ type: 'warning', message: 'Ken couldn’t start.', detail: error.message, buttons: ['Try Again', 'Quit'], defaultId: 0, cancelId: 1 });
      if (response !== 0) return null;
    }
  }
}

// Keep the window on a working engine: if it stops answering for 15 seconds, bring it back.
function watchEngine() {
  let misses = 0, healing = false;
  setInterval(async () => {
    if (healing || quitting) return;
    if ((await running(service())) !== null) { misses = 0; return; }
    if (++misses < 3) return;
    healing = true; misses = 0;
    await win.loadFile(path.join(__dirname, 'loading.html'));
    const found = await connectOrAsk();
    if (!found) { quitting = true; app.quit(); return; }
    await win.loadURL(found.origin);
    healing = false;
  }, 5000);
}

// Updates download in the background and install while the window is closed and Ken
// isn't mid-task; the app then relaunches straight back into the menu bar.
// Updates stay out of the chat. Once one has downloaded, the window shows "Ken x.y.z is
// ready" with a Restart now button, the "Check for Updates…" item in the Ken menu, the
// menu-bar icon and Settings becomes "Restart to update to x.y.z", and quitting installs it.
// A check the user asked for (menu or Settings) reports what it found with a quiet toast.
const quietRelaunch = path.join(home, 'desktop-data', 'updated');
let updateReady = false, updateVersion = '', installing = false, updateChecks;
// Everyone gets stable releases, including a Mac that installed a beta or once chose Beta.
const updateChannel = 'stable';
// If an update doesn't install, Ken must come back and say so rather than vanish and retry
// forever: a note records the version being installed and which Ken.app was on disk, a
// timer reopens Ken if the installer didn't, and the next start that finds the note with
// the same Ken.app still on disk tells the user to download the new version (nothing can
// fix an app whose updater is broken but a fresh download).
const installingNote = path.join(home, 'desktop-data', 'installing');
const bundlePath = path.resolve(process.execPath, '..', '..', '..');
// Which Ken.app is on disk: a replaced bundle is a different directory (or a rewritten Info.plist).
const bundleStamp = () => { try { const b = statSync(bundlePath), p = statSync(path.join(bundlePath, 'Contents', 'Info.plist')); return `${b.ino}:${p.ino}:${p.mtimeMs}`; } catch { return ''; } };
let updateFailed = '';
const downloadUrl = (version) => `https://github.com/prasann16/kencomputer/releases/download/v${encodeURIComponent(version)}/Ken-${process.arch === 'arm64' ? 'arm64' : 'x64'}.dmg`;
const failedState = () => updateFailed && acceptsVersion(updateChannel, updateFailed) ? { state: 'failed', version: updateFailed, url: downloadUrl(updateFailed) } : null;
const tellPage = (state) => { if (win && !win.isDestroyed()) win.webContents.send('update:state', state); };
async function engineBusy() {
  try {
    const { token, origin } = service();
    return (await (await fetch(origin + '/api/meta', { headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(2000) })).json()).busy;
  } catch { return false; }
}
// macOS refuses to replace Ken.app while anything inside it is running, and the engine
// lives inside it: with the engine up, the install is cancelled and Ken never comes back.
// So the engine (and its launchd agent, or it comes straight back) stops first; the
// relaunched app starts the new engine itself.
async function stopService() {
  await run('/bin/launchctl', ['bootout', `gui/${process.getuid()}/${appAgent}`]);
  await stopOtherEngine();
}
async function restartIntoUpdate(quiet, reservedByQuit = false) {
  if ((installing && !reservedByQuit) || !updateReady || !acceptsVersion(updateChannel, updateVersion)) return;
  installing = true;
  if (quiet) writeFileSync(quietRelaunch, '');
  quitting = true;
  writeFileSync(installingNote, `${updateVersion}\n${bundleStamp()}`);
  require('node:child_process').spawn('/bin/sh', ['-c', 'sleep 120; pgrep -x Ken >/dev/null || open "$0"', bundlePath], { detached: true, stdio: 'ignore' }).unref();
  await stopService();
  require('electron-updater').autoUpdater.quitAndInstall(true, true);
}
async function installIfIdle() {
  if (updateReady && updateVersion !== updateFailed && !win?.isVisible() && !(await engineBusy())) restartIntoUpdate(true);
}
async function installNow() {
  if (!updateReady) return;
  if (await engineBusy()) { tellPage({ state: 'busy' }); return; }
  restartIntoUpdate(false);
}
function checkNow(manual) {
  if (!app.isPackaged) { if (manual) tellPage({ state: 'none', version: app.getVersion() }); return; }
  updateChecks?.check(manual);
}
function checkForUpdates() {
  if (!app.isPackaged) return;
  const { autoUpdater } = require('electron-updater');
  // The release check feeds the app a local "newer" copy of itself to prove it can update.
  if (process.env.KEN_UPDATE_URL) autoUpdater.setFeedURL({ provider: 'generic', url: process.env.KEN_UPDATE_URL });
  else autoUpdater.setFeedURL({ provider: 'custom', updateProvider: require('./github-updates.cjs').GitHubUpdates,
    owner: 'prasann16', repo: 'kencomputer' });
  updateChecks = new UpdateChecks(autoUpdater, updateChannel, (state) => {
    if (state.state === 'channel') { updateReady = false; updateVersion = ''; buildMenus(); }
    if (state.state === 'ready') { updateReady = true; updateVersion = state.version; buildMenus(); }
    tellPage({ version: app.getVersion(), ...state });
    if (state.state === 'ready') installIfIdle();
  }, { testFeed: !!process.env.KEN_UPDATE_URL });
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
  // Restore before opening the loading page, which follows nativeTheme via CSS.
  try {
    const saved = readFileSync(themeFile, 'utf8').trim();
    if (isTheme(saved)) nativeTheme.themeSource = saved;
  } catch {}
  win = new BrowserWindow({ title: 'Ken', width: 900, height: 700, minWidth: 480, minHeight: 420, backgroundColor: windowBackground(), show: false,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default', trafficLightPosition: { x: 20, y: 21 },
    icon: path.join(__dirname, 'icon.png'), webPreferences: { preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
  win.on('close', (event) => { if (!quitting) { event.preventDefault(); win.hide(); } });
  win.on('hide', installIfIdle);
  nativeTheme.on('updated', () => { if (win && !win.isDestroyed()) win.setBackgroundColor(windowBackground()); });
  const updated = existsSync(quietRelaunch);
  if (updated) rmSync(quietRelaunch);
  try {
    const [version, stamp] = readFileSync(installingNote, 'utf8').split('\n'); rmSync(installingNote);
    if (stamp === bundleStamp()) updateFailed = version;  // Ken.app wasn't replaced: the install failed
  } catch {}
  // A marker for the release check (and anyone debugging): this start follows a failed update.
  const failedMarker = path.join(home, 'desktop-data', 'update-failed');
  if (updateFailed) writeFileSync(failedMarker, updateFailed); else rmSync(failedMarker, { force: true });
  win.once('ready-to-show', () => { if (!updated || updateFailed) win.show(); });
  await win.loadFile(path.join(__dirname, 'loading.html'));

  const found = await connectOrAsk();
  if (!found) { quitting = true; app.quit(); return; }
  const { origin, token } = found;
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
  ipcMain.handle('theme:set', (event, preference) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    if (!isTheme(preference)) throw new Error('Invalid appearance.');
    if (nativeTheme.themeSource === preference) return;
    nativeTheme.themeSource = preference;
    mkdirSync(path.dirname(themeFile), { recursive: true });
    writeFileSync(themeFile, preference);
  });
  ipcMain.handle('update:failed', (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    return failedState();
  });
  // Settings: this version, an update waiting to install, check now, restart into it.
  ipcMain.handle('update:status', (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    return { version: app.getVersion(), channel: updateChannel, ready: updateReady && updateVersion !== updateFailed ? updateVersion : null };
  });
  ipcMain.handle('update:check', (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    checkNow(true);
  });
  ipcMain.handle('update:install', (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    return installNow();
  });
  // Ken on your phone: a BotFather code copied on the phone (iPhone's clipboard reaches the Mac).
  // Only something shaped like a bot token is passed to the page, never the rest of the clipboard.
  ipcMain.handle('clipboard:bot-token', async (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    const found = String(await clipboard.readText()).match(/\b\d{6,}:[A-Za-z0-9_-]{30,}/);
    return found ? found[0] : null;
  });
  ipcMain.handle('microphone:permission', (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    return requestMicrophone(systemPreferences, process.platform);
  });
  // A native menu (e.g. a project's ⋯ or right-click); resolves with the chosen item's id, or null.
  ipcMain.handle('menu:popup', (event, items) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    return new Promise((resolve) => {
      let chosen = null;
      const menu = Menu.buildFromTemplate(items.map((i) => i.separator ? { type: 'separator' } : { label: String(i.label), click: () => { chosen = i.id; } }));
      menu.popup({ window: win, callback: () => setImmediate(() => resolve(chosen)) });
    });
  });
  ipcMain.handle('chrome:connect', (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    connectChrome();
  });
  ipcMain.handle('folder:open', async (event, folder) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    if (typeof folder === 'string' && existsSync(folder)) await shell.openPath(folder);
  });
  ipcMain.handle('folder:pick', async (event) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    const { canceled, filePaths } = await dialog.showOpenDialog(win, { properties: ['openDirectory', 'createDirectory'], buttonLabel: 'Use this folder' });
    return canceled ? '' : filePaths[0];
  });
  ipcMain.handle('microphone:settings', async (event, kind) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    if (process.platform === 'darwin') await shell.openExternal(kind === 'privacy' ? 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone' : 'x-apple.systempreferences:com.apple.preference.sound?input');
  });
  await win.loadURL(origin);

  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'icon.png')).resize({ width: 20, height: 20 }));
  tray.setToolTip('Ken');
  tray.on('click', show);
  buildMenus();
  checkForUpdates();
  watchEngine();
}

// One item covers updates: it checks, or, once an update has downloaded, restarts into it.
function updateItem() {
  return updateReady
    ? { label: `Restart to update to ${updateVersion}`, click: () => installNow() }
    : { label: 'Check for Updates…', click: () => { show(); checkNow(true); } };
}
// Ken uses the user's own Chrome; Chrome needs remote debugging switched on (again after it
// restarts). Chrome ignores chrome:// links handed to it by `open`, so ask Chrome to open the tab;
// if that's refused, bring Chrome forward with the address on the clipboard to paste.
const CHROME_PAGE = 'chrome://inspect/#remote-debugging';
function connectChrome() {
  const script = `tell application "Google Chrome"
    activate
    if (count of windows) is 0 then make new window
    tell front window to make new tab with properties {URL:"${CHROME_PAGE}"}
  end tell`;
  execFile('/usr/bin/osascript', ['-e', script], (error) => {
    if (!error) return;
    clipboard.writeText(CHROME_PAGE);
    execFile('open', ['-a', 'Google Chrome'], (missing) => {
      if (missing) dialog.showMessageBox({ message: 'Ken uses Google Chrome to work on websites.', detail: 'Install Chrome, then choose Connect Chrome… again.' });
    });
  });
}
function buildMenus() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: app.name, submenu: [{ role: 'about' }, updateItem(), { type: 'separator' }, { label: 'Connect Chrome…', click: connectChrome }, { type: 'separator' }, { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' }, { type: 'separator' }, { role: 'quit' }] },
    { role: 'editMenu' },
    { label: 'View', submenu: [{ role: 'reload' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }, ...(!app.isPackaged ? [{ role: 'toggleDevTools' }] : [])] },
    { role: 'windowMenu' },
  ]));
  if (tray) tray.setContextMenu(Menu.buildFromTemplate([{ label: 'Open Ken', click: show }, updateItem(), { type: 'separator' }, { label: 'Quit', click: () => app.quit() }]));
}

// Closing the window only hides Ken, but a quit asked for by macOS (log out, shut down),
// a signal or the Quit menu must go through, not be cancelled by that.
app.on('before-quit', () => { quitting = true; });
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', show);
  app.whenReady().then(async () => {
    buildMenus();
    try { await start(); } catch (error) { dialog.showErrorBox('Could not open Ken', error.message); app.quit(); }
  });
  app.on('activate', show);
  // Quitting the app leaves the ken service (and Telegram) running. With an update waiting,
  // quitting installs it: the engine has to stop for macOS to replace Ken.app, so Ken comes
  // back quietly in the menu bar on the new version. Mid-task, the quit just goes through.
  app.on('before-quit', (event) => {
    quitting = true;
    if (!updateReady || installing || updateVersion === updateFailed || process.platform !== 'darwin') return;
    event.preventDefault();
    installing = true;
    engineBusy().then((busy) => busy ? app.exit(0) : restartIntoUpdate(true, true));
  });
}
