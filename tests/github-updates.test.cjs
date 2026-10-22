const { test } = require('node:test');
const assert = require('node:assert/strict');
const { AppUpdater } = require('electron-updater');
const { GitHubUpdates } = require('../desktop/github-updates.cjs');
const { UpdateChecks } = require('../desktop/updates.cjs');

// Exercise the real updater/provider with only the app and HTTP transport replaced.
function setup({ tags, stable = 'v0.1.23', installed = '0.1.23', channel = 'beta' }) {
  const requests = [], states = [];
  const updater = new AppUpdater(null, { version: installed, isPackaged: true, whenReady: async () => {} });
  updater.logger = null;
  updater.autoDownload = false;
  updater._testOnlyOptions = { platform: 'darwin' };
  updater.stagingUserIdPromise = { value: Promise.resolve('test-user') };
  updater.httpExecutor = { async request({ path }) {
    requests.push(path);
    if (path.endsWith('.atom')) return '<feed>' + tags.map(tag =>
      `<entry><link href="https://github.com/prasann16/kencomputer/releases/tag/${tag}"/><title>${tag}</title><content>Notes for ${tag}</content></entry>`
    ).join('') + '</feed>';
    if (path.endsWith('/latest')) return JSON.stringify({ tag_name: stable });
    const match = /\/download\/([^/]+)\/(beta|latest)-mac\.yml$/.exec(path);
    assert.ok(match, `Unexpected request: ${path}`);
    const [, tag, manifestChannel] = match;
    assert.ok(tags.includes(tag), `Unknown release: ${tag}`);
    assert.equal(manifestChannel, tag.includes('-beta.') ? 'beta' : 'latest');
    return `version: ${tag.replace(/^v/, '')}\nfiles:\n  - url: Ken-arm64.zip\n    sha512: test-checksum\n`;
  } };
  updater.setFeedURL({ provider: 'custom', updateProvider: GitHubUpdates, owner: 'prasann16', repo: 'kencomputer' });
  const checks = new UpdateChecks(updater, channel, state => states.push(state));
  return { updater, checks, requests, states };
}

test('a stable release published after a newer beta cannot hide that beta', async () => {
  const { updater, checks, requests, states } = setup({ tags: ['v0.1.23', 'v0.2.0-beta.2'] });
  await checks.check(true);
  assert.deepEqual(states, [{ state: 'downloading', version: '0.2.0-beta.2' }]);
  assert.ok(requests.at(-1).endsWith('/v0.2.0-beta.2/beta-mac.yml'));
  const { info, provider } = updater.updateInfoAndProvider;
  assert.equal(info.releaseName, 'v0.2.0-beta.2');
  assert.equal(info.releaseNotes, 'Notes for v0.2.0-beta.2');
  assert.equal(provider.resolveFiles(info)[0].url.href,
    'https://github.com/prasann16/kencomputer/releases/download/v0.2.0-beta.2/Ken-arm64.zip');
});

test('beta selection compares versions numerically and ignores other prerelease channels', async () => {
  const { checks, states } = setup({ tags: ['v9.0.0-alpha.1', 'not-a-version', 'v0.2.0-rc.1', 'v0.2.0-beta.9', 'v0.2.0-beta.10'] });
  await checks.check(true);
  assert.deepEqual(states, [{ state: 'downloading', version: '0.2.0-beta.10' }]);
});

test('beta users receive the newer stable version even when an older beta is first in the feed', async () => {
  const { checks, requests, states } = setup({ tags: ['v0.2.0-beta.10', 'v0.2.0'], installed: '0.2.0-beta.9' });
  await checks.check(true);
  assert.deepEqual(states, [{ state: 'downloading', version: '0.2.0' }]);
  assert.ok(requests.at(-1).endsWith('/v0.2.0/latest-mac.yml'));
});

test('stable checks use GitHub latest and switching back never downgrades an installed beta', async () => {
  const tags = ['v0.2.0-beta.2', 'v0.1.23'];
  const stable = setup({ tags, installed: '0.1.22', channel: 'stable' });
  await stable.checks.check(true);
  assert.deepEqual(stable.states, [{ state: 'downloading', version: '0.1.23' }]);
  assert.ok(stable.requests.at(-1).endsWith('/v0.1.23/latest-mac.yml'));

  const beta = setup({ tags, installed: '0.2.0-beta.2' });
  await beta.checks.check(true);
  assert.deepEqual(beta.states, [{ state: 'none' }]);
  beta.checks.select('stable');
  await beta.checks.pending;
  assert.equal(beta.states.at(-1).state, 'none');
  assert.ok(beta.requests.at(-1).endsWith('/v0.1.23/latest-mac.yml'));
  assert.equal(beta.updater.allowDowngrade, false);
});
