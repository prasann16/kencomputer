const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { UpdateChecks, initialChannel, acceptsVersion } = require('../desktop/updates.cjs');

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
};
function setup(channel = 'stable', options) {
  const updater = new EventEmitter(), states = [], requests = [];
  updater.checkForUpdates = () => {
    const request = deferred(); requests.push(request); return request.promise;
  };
  const checks = new UpdateChecks(updater, channel, (state) => states.push(state), options);
  return { updater, states, requests, checks };
}

test('stable installs stay stable; installing a beta opts in unless a preference is saved', () => {
  assert.equal(initialChannel('', '0.1.22'), 'stable');
  assert.equal(initialChannel('', '0.1.22-beta.1'), 'beta');
  assert.equal(initialChannel('stable', '0.1.22-beta.1'), 'stable');
  assert.equal(initialChannel('beta', '0.1.22'), 'beta');
  assert.equal(initialChannel('garbage', '0.1.22'), 'stable');
  assert.equal(acceptsVersion('stable', '0.1.23-beta.1'), false);
  assert.equal(acceptsVersion('beta', '0.1.23'), true);
  assert.equal(acceptsVersion('beta', '0.1.23-alpha.1'), false);
});

test('channel selection disables downgrades and automatic installs on quit', async () => {
  const { checks, updater, requests } = setup();
  assert.equal(updater.channel, 'latest');
  assert.equal(updater.allowPrerelease, false);
  assert.equal(updater.autoInstallOnAppQuit, false);
  checks.select('beta');
  assert.equal(updater.channel, 'beta');
  assert.equal(updater.allowPrerelease, true);
  assert.equal(updater.allowDowngrade, false);
  requests[0].resolve(null);
  await checks.pending;
  checks.select('stable');
  assert.equal(updater.allowPrerelease, false);
  assert.equal(updater.allowDowngrade, false);
  requests[1].resolve(null);
  await checks.pending;
  assert.throws(() => checks.select('alpha'), /Invalid/);
});

test('switching to stable cancels a beta download and ignores its late completion', async () => {
  const { checks, updater, requests, states } = setup('beta');
  const download = deferred();
  let cancelled = false;
  const pending = checks.check(true);
  requests[0].resolve({ downloadPromise: download.promise, cancellationToken: { cancel() { cancelled = true; } } });
  await Promise.resolve();
  checks.select('stable');
  assert.equal(cancelled, true);
  assert.equal(requests.length, 1); // wait for the old download before starting another
  updater.emit('update-downloaded', { version: '0.1.23-beta.1' });
  assert.equal(states.some((s) => s.state === 'ready'), false);
  download.resolve();
  await pending;
  assert.equal(requests.length, 2);
  updater.emit('update-downloaded', { version: '0.1.23' });
  assert.deepEqual(states.at(-1), { state: 'ready', version: '0.1.23' });
  requests[1].resolve(null);
  await checks.pending;
});

test('a channel change during the metadata request also cancels its eventual download', async () => {
  const { checks, updater, requests, states } = setup('beta');
  let cancelled = false;
  const pending = checks.check(true);
  checks.select('stable');
  updater.emit('update-not-available');
  assert.deepEqual(states, [{ state: 'channel', channel: 'stable' }]);
  requests[0].resolve({ cancellationToken: { cancel() { cancelled = true; } }, downloadPromise: Promise.resolve() });
  await pending;
  assert.equal(cancelled, true);
  assert.equal(requests.length, 2);
  updater.emit('update-not-available');
  assert.equal(states.at(-1).state, 'none');
  requests[1].resolve(null);
  await checks.pending;
});

test('release self-update checks use the local latest feed even in a beta build', () => {
  const { updater } = setup('beta', { testFeed: true });
  assert.equal(updater.channel, 'latest');
  assert.equal(updater.allowDowngrade, false);
});

// Run the real main-process quit hooks with isolated OS/Electron adapters. In particular,
// its in-progress guard must allow the install reserved by before-quit to continue.
test('quitting installs a ready update only after the engine is idle and stopped', async () => {
  const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../desktop/main.cjs'), 'utf8');
  for (const busy of [false, true]) {
    const calls = [], app = new EventEmitter(), updater = new EventEmitter();
    Object.assign(app, {
      setName() {}, setPath() {}, getVersion: () => '0.1.22', isPackaged: true,
      requestSingleInstanceLock: () => true, whenReady: () => new Promise(() => {}),
      exit: () => calls.push('exit'),
    });
    updater.checkForUpdates = async () => null;
    updater.quitAndInstall = () => calls.push('install');
    const electron = { app, ipcMain: {}, nativeTheme: {}, Menu: { buildFromTemplate: (t) => t, setApplicationMenu() {} } };
    const adapters = {
      electron, 'electron-updater': { autoUpdater: updater },
      'node:fs': { existsSync: () => false, readFileSync() { throw Error('Not found'); }, writeFileSync() {} },
      'node:child_process': {
        execFile: (_cmd, _args, _options, done) => { calls.push('stop'); done(); },
        spawn: () => ({ unref() {} }),
      },
      './updates.cjs': require('../desktop/updates.cjs'),
      './microphone.cjs': {}, './security.cjs': {},
    };
    vm.runInNewContext(source + '\nwin = fixtureWindow; checkForUpdates();', {
      require: (name) => adapters[name] || require(name),
      process: { env: {}, execPath: '/Applications/Ken.app/Contents/MacOS/Ken', platform: 'darwin', getuid: () => 501 },
      __dirname: path.join(__dirname, '../desktop'), console, AbortSignal,
      setInterval() {}, fetch: async () => ({ json: async () => ({ busy }) }),
      fixtureWindow: { isVisible: () => true, isDestroyed: () => false, webContents: { send() {} } },
    });
    assert.equal(updater.autoInstallOnAppQuit, false);
    updater.emit('update-downloaded', { version: '0.1.23' });
    let prevented = false;
    app.emit('before-quit', { preventDefault() { prevented = true; } });
    await new Promise(setImmediate);
    assert.equal(prevented, true);
    assert.deepEqual(calls, busy ? ['exit'] : ['stop', 'install']);
  }
});
