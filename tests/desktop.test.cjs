const { test } = require('node:test');
const assert = require('node:assert/strict');
const { trustedFrame } = require('../desktop/security.cjs');
const { requestMicrophone } = require('../desktop/microphone.cjs');
test('only the app main frame can use native controls', () => {
  const frame = { url: 'http://127.0.0.1:7777/' };
  const wc = { mainFrame: frame };
  const win = { webContents: wc, isDestroyed: () => false };
  assert.equal(trustedFrame({ sender: wc, senderFrame: frame }, win, 'http://127.0.0.1:7777'), true);
  assert.equal(trustedFrame({ sender: wc, senderFrame: { url: frame.url } }, win, 'http://127.0.0.1:7777'), false);
  assert.equal(trustedFrame({ sender: {}, senderFrame: frame }, win, 'http://127.0.0.1:7777'), false);
});
test('microphone permission reuses a grant and asks only when undecided', async () => {
  const prefs = (status) => ({ getMediaAccessStatus: () => status, askForMediaAccess: async () => 'asked' });
  assert.equal(await requestMicrophone(prefs('granted'), 'darwin'), true);
  assert.equal(await requestMicrophone(prefs('not-determined'), 'darwin'), 'asked');
  assert.equal(await requestMicrophone(prefs('denied'), 'darwin'), false);
});
