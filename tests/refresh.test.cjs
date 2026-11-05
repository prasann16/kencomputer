// Ken in the cloud picks up a new version of its screens by itself (web/refresh.js), but only when nothing is lost.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');

const source = readFileSync(require.resolve('../web/refresh.js'), 'utf8');

// A page: its stamp, what's on it (fields with values, open parts), and the site's current version.
function page({ stamp = 'v1', current = 'v1', fields = [], open = [], desktop = false } = {}) {
  const listeners = {}, timers = [], reloads = [];
  const doc = {
    visibilityState: 'visible',
    querySelector: (sel) => {
      if (sel === 'meta[name="ken-ui-version"]') return stamp ? { getAttribute: () => stamp } : null;
      return sel.split(', ').some((s) => open.includes(s)) ? {} : null;
    },
    querySelectorAll: () => fields.map((value) => ({ value })),
    addEventListener: (name, fn) => { listeners[name] = fn; },
  };
  const win = {
    document: doc, kenDesktop: desktop ? {} : undefined,
    location: { reload: () => reloads.push(1) },
    fetch: async (url) => ({ ok: true, json: async () => ({ version: page.current ?? current }) }),
  };
  const context = { window: win, setInterval: (fn) => { timers.push(fn); return 1; }, clearInterval() {}, setTimeout: (fn) => { timers.push(fn); return 2; }, clearTimeout() {} };
  runInNewContext(source, context);
  return { win, doc, reloads, listeners, timers, refresh: win.kenRefresh };
}

test('a new version: the page reloads by itself, at the same address', async () => {
  const p = page({ current: 'v2' });
  await p.refresh.running.check();
  assert.equal(p.reloads.length, 1);
});

test('the same version: nothing happens', async () => {
  const p = page();
  await p.refresh.running.check();
  assert.equal(p.reloads.length, 0);
});

test('never while something would be lost: a typed message, an upload, a recording, a popup', async () => {
  for (const state of [{ fields: ['half a message'] }, { open: ['.upload-status'] }, { open: ['.recording-input'] }, { open: ['.modal'] }, { open: ['.talk'] }]) {
    const p = page({ current: 'v2', ...state });
    await p.refresh.running.check();
    assert.equal(p.reloads.length, 0, JSON.stringify(state));
    assert.ok(p.timers.length > 1, 'tries again later');
  }
  assert.equal(page().refresh.safeToReload({ querySelectorAll: () => [{ value: '  ' }], querySelector: () => null }), true); // only spaces
});

test('waits, then reloads once it is safe', async () => {
  const fields = ['typing'];
  const p = page({ current: 'v2', fields });
  await p.refresh.running.check();
  assert.equal(p.reloads.length, 0);
  fields.length = 0;  // sent
  p.timers.at(-1)();  // the retry
  assert.equal(p.reloads.length, 1);
});

test('coming back to the tab checks at once', async () => {
  const p = page({ current: 'v2' });
  await p.listeners.visibilitychange();
  await new Promise((r) => setImmediate(r));
  assert.equal(p.reloads.length, 1);
});

test('off in the Mac app, and on a page without a stamp (Ken on your own computer)', () => {
  assert.equal(page({ desktop: true }).refresh.running, null);
  assert.equal(page({ stamp: null }).refresh.running, null);
});
