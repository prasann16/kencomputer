const { test } = require('node:test');
const assert = require('node:assert/strict');
const { trustedFrame } = require('../desktop/security.cjs');
const { requestMicrophone } = require('../desktop/microphone.cjs');
const { localComputerFrame } = require('../desktop/security.cjs');
test('remote views keep native UI access but cannot operate on local folders or Chrome', () => {
  const frame = { url: 'http://127.0.0.1:7777/computer/0123456789abcdef' };
  const wc = { mainFrame: frame };
  const win = { webContents: wc, isDestroyed: () => false };
  const event = { sender: wc, senderFrame: frame };
  assert.equal(trustedFrame(event, win, 'http://127.0.0.1:7777'), true);
  assert.equal(localComputerFrame(event, win, 'http://127.0.0.1:7777'), false);
  frame.url = 'http://127.0.0.1:7777/';
  assert.equal(localComputerFrame(event, win, 'http://127.0.0.1:7777'), true);
});
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
