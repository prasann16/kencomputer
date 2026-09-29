const { test } = require('node:test');
const assert = require('node:assert/strict');
const { webUrl, trustedFrame } = require('../desktop/security.cjs');
test('browser rejects local app URLs and executable schemes', () => {
  for (const url of ['file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,x', 'http://localhost:7777', 'http://127.0.0.1', 'http://2130706433', 'http://[::1]', 'https://user:pass@example.com']) assert.throws(() => webUrl(url));
  assert.equal(webUrl('example.com'), 'https://example.com/');
  assert.equal(webUrl('https://example.com/path?q=1'), 'https://example.com/path?q=1');
});
test('only the app main frame can use native browser controls', () => {
  const frame = {url:'http://127.0.0.1:7777/'};
  const wc = {mainFrame:frame};
  const win = {webContents:wc,isDestroyed:()=>false};
  assert.equal(trustedFrame({sender:wc,senderFrame:frame},win,'http://127.0.0.1:7777'), true);
  assert.equal(trustedFrame({sender:wc,senderFrame:{url:frame.url}},win,'http://127.0.0.1:7777'), false);
  assert.equal(trustedFrame({sender:{},senderFrame:frame},win,'http://127.0.0.1:7777'), false);
});
const { KenBrowser } = require('../desktop/browser.cjs');
test('disconnect cancels queued work and prevents automatic reconnect', async () => {
  const browser = new KenBrowser({ isDestroyed: () => true }, { endpoint: 'http://127.0.0.1:1' });
  const pending = browser.action({ action: 'navigate', url: 'https://example.com' });
  await browser.action({ action: 'disconnect' });
  await assert.rejects(pending, /disconnected/);
  await assert.rejects(browser.action({ action: 'read' }), /disconnected/);
  assert.equal(browser.state().paused, true);
  assert.equal(browser.state().connecting, false);
});
test('unavailable Chrome leaves actionable setup state without losing the chat', async () => {
  const browser = new KenBrowser({ isDestroyed: () => true }, { endpoint: 'http://127.0.0.1:1' });
  await assert.rejects(browser.action({ action: 'connect' }), /Open Chrome settings/);
  assert.equal(browser.state().connected, false);
  assert.equal(browser.state().connecting, false);
  assert.equal(browser.state().loading, false);
  assert.match(browser.state().error, /Allow remote debugging/);
});


test('microphone consent is reused; denied access never loops through prompts', async () => {
  const { requestMicrophone } = require('../desktop/microphone.cjs');
  let state='not-determined', prompts=0;
  const preferences={getMediaAccessStatus:()=>state,askForMediaAccess:async(type)=>{assert.equal(type,'microphone');prompts++;state='granted';return true;}};
  assert.equal(await requestMicrophone(preferences,'darwin'),true);
  assert.equal(await requestMicrophone(preferences,'darwin'),true);
  assert.equal(prompts,1);
  state='denied';assert.equal(await requestMicrophone(preferences,'darwin'),false);assert.equal(prompts,1);
});
