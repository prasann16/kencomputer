// One design for the whole app: the rules (colours, sizes, buttons, cards, fields…) live in web/theme.css, and the
// screens (style.css) and the Mac's density (desktop.css) only use them.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const web = (name) => readFileSync(join(__dirname, '..', 'web', name), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const theme = web('theme.css'), screens = web('style.css'), mac = web('desktop.css');
const defined = new Set([...theme.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));

test('the design rules load first, then the screens, then the Mac', () => {
  const order = [...readFileSync(join(__dirname, '..', 'web', 'index.html'), 'utf8').matchAll(/href="\/(\w+)\.css"/g)].map((m) => m[1]);
  assert.deepEqual(order, ['theme', 'style', 'desktop']);
});

test('every colour and size the screens use is one of the shared ones', () => {
  for (const [name, css] of [['style.css', screens], ['desktop.css', mac]]) {
    const used = new Set([...css.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]));
    const own = [...css.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]);
    for (const v of used) assert.ok(defined.has(v) || own.includes(v), `${name} uses ${v}, which theme.css doesn't define`);
    // A screen may set its own moving parts (Ken's face follows the voices), never a new colour or size token.
    for (const v of own) assert.ok(['--out', '--in'].includes(v) || (name === 'desktop.css' && defined.has(v)), `${name} defines ${v}: add it to theme.css instead`);
  }
});

test('light and dark have the same colours', () => {
  const block = (sel) => theme.slice(theme.indexOf(sel)).split('}')[0];
  const names = (b) => [...b.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]).sort();
  assert.deepEqual(names(block(":root[data-theme='dark']")), names(block(':root {')));
});
