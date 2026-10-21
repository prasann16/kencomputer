const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');

const source = readFileSync(require.resolve('../web/theme.js'), 'utf8');
function page({ saved = null, dark = false, blocked = false } = {}) {
  const events = {}, mediaEvents = {}, native = [];
  const storage = {
    value: saved,
    getItem() { if (blocked) throw new Error('Storage blocked'); return this.value; },
    setItem(_key, value) { if (blocked) throw new Error('Storage blocked'); this.value = value; },
  };
  const media = { matches: dark, addEventListener: (name, callback) => { mediaEvents[name] = callback; } };
  const root = { dataset: {} }, meta = {};
  const window = {
    matchMedia: () => media,
    addEventListener: (name, callback) => { events[name] = callback; },
    dispatchEvent: () => {},
    kenDesktop: { setTheme: async (value) => { native.push(value); } },
  };
  runInNewContext(source, {
    window, localStorage: storage, Event,
    document: { documentElement: root, querySelector: () => ({ setAttribute: (key, value) => { meta[key] = value; } }) },
  });
  return {
    theme: window.kenTheme, root, meta, storage, native,
    system(value) { media.matches = value; mediaEvents.change(); },
    storageEvent(value, key = 'ken-theme', storageArea = storage) { events.storage({ newValue: value, key, storageArea }); },
  };
}

test('first paint follows the system, including later system changes', () => {
  const p = page({ dark: true });
  assert.equal(p.theme.preference, 'system');
  assert.equal(p.root.dataset.theme, 'dark');
  assert.equal(p.meta.content, '#181818');
  p.system(false);
  assert.equal(p.root.dataset.theme, 'light');
  assert.equal(p.meta.content, '#ffffff');
});

test('explicit appearance persists across launches and overrides the system until reset', () => {
  const p = page();
  p.theme.setPreference('dark');
  assert.equal(p.root.dataset.theme, 'dark');
  assert.equal(p.storage.value, 'dark');
  assert.equal(p.native.at(-1), 'dark');
  assert.equal(page({ saved: p.storage.value }).root.dataset.theme, 'dark');
  p.system(false);
  assert.equal(p.root.dataset.theme, 'dark');
  p.theme.setPreference('system');
  assert.equal(p.root.dataset.theme, 'light');
  assert.equal(p.native.at(-1), 'system');
  const light = page({ saved: 'light', dark: true });
  assert.equal(light.root.dataset.theme, 'light');
});

test('unavailable storage and invalid saved values still allow appearance changes', () => {
  assert.equal(page({ saved: 'invalid', dark: true }).root.dataset.theme, 'dark');
  const p = page({ blocked: true });
  p.theme.setPreference('dark');
  assert.equal(p.root.dataset.theme, 'dark');
  p.theme.setPreference('invalid');
  assert.equal(p.theme.preference, 'system');
  assert.equal(p.root.dataset.theme, 'light');
});

test('other windows update appearance, while unrelated storage changes are ignored', () => {
  const p = page({ saved: 'light' });
  p.storageEvent('dark', 'unrelated');
  p.storageEvent('dark', 'ken-theme', {});
  assert.equal(p.root.dataset.theme, 'light');
  p.storageEvent('dark');
  assert.equal(p.root.dataset.theme, 'dark');
  assert.equal(p.theme.preference, 'dark');
  p.storageEvent(null, null);
  assert.equal(p.theme.preference, 'system');
  assert.equal(p.root.dataset.theme, 'light');
});
