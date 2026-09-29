// Ken for Mac: a window onto the local `ken` service (bot.py). The service owns
// the engine, Telegram, voice and schedules; this app only shows its chat.
const { app, BrowserWindow, ipcMain, Menu, dialog, shell, session, Tray, nativeImage, systemPreferences } = require('electron');
const { execFile } = require('node:child_process');
const { readFileSync, existsSync } = require('node:fs');
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

async function up({ origin, token }) {
  if (!token) return false;
  try { return (await fetch(origin + '/api/meta', { headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(1000) })).ok; }
  catch { return false; }
}

// Connect to the running service, starting it with `ken start` if needed.
async function connect() {
  if (await up(service())) return service();
  const ken = [path.join(os.homedir(), '.local/bin/ken'), '/usr/local/bin/ken', '/opt/homebrew/bin/ken'].find(existsSync);
  if (!ken) throw new Error('Ken isn’t installed on this Mac yet. In Terminal, run:\n\ncurl -fsSL https://kencomputer.dev/install | bash');
  await new Promise((resolve) => execFile(ken, ['start'], { env: { ...process.env, KEN_HOME: home } }, () => resolve()));
  for (let i = 0; i < 60; i++) {
    if (await up(service())) return service();
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('Ken’s service didn’t start. Run “ken logs” in Terminal to see why.');
}

function external(url) {
  try { if (['https:', 'http:', 'mailto:'].includes(new URL(url).protocol)) shell.openExternal(url); } catch {}
}

function show() { if (win) { win.show(); win.focus(); } }

async function start() {
  win = new BrowserWindow({ title: 'Ken', width: 900, height: 700, minWidth: 480, minHeight: 420, backgroundColor: '#ffffff', show: false,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default', trafficLightPosition: { x: 20, y: 21 },
    icon: path.join(__dirname, 'icon.png'), webPreferences: { preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
  win.on('close', (event) => { if (!quitting) { event.preventDefault(); win.hide(); } });
  win.once('ready-to-show', () => win.show());
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
  ipcMain.handle('microphone:settings', async (event, kind) => {
    if (!trustedFrame(event, win, origin)) throw new Error('Untrusted request.');
    if (process.platform === 'darwin') await shell.openExternal(kind === 'privacy' ? 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone' : 'x-apple.systempreferences:com.apple.preference.sound?input');
  });
  await win.loadURL(origin);

  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'icon.png')).resize({ width: 20, height: 20 }));
  tray.setToolTip('Ken');
  tray.setContextMenu(Menu.buildFromTemplate([{ label: 'Open Ken', click: show }, { type: 'separator' }, { label: 'Quit', click: () => app.quit() }]));
  tray.on('click', show);
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', show);
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      { role: 'appMenu' },
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
