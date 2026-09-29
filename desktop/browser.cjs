const { chromium } = require('playwright-core');
const { webUrl } = require('./security.cjs');

// Attach to the user's running Chrome. Never launch a replacement profile or
// close their browser. Playwright's close on a CDP connection only detaches.
class KenBrowser {
  constructor(win, { endpoint = 'chrome', openChrome = async () => {} } = {}) {
    this.win = win;
    this.endpoint = endpoint;
    this.openChrome = openChrome;
    this.browser = null;
    this.page = null;
    this.elements = [];
    this.error = '';
    this.title = '';
    this.loading = false;
    this.connecting = false;
    this.paused = false;
    this.epoch = 0;
    this.queue = Promise.resolve();
  }
  state() {
    return { connected: !!this.browser?.isConnected(), connecting: this.connecting, paused: this.paused,
      loading: this.loading, title: this.title, url: this.page?.isClosed() === false ? this.page.url() : '', error: this.error };
  }
  changed() { if (!this.win.isDestroyed()) this.win.webContents.send('browser:state', this.state()); }
  async connect(epoch) {
    if (this.browser?.isConnected()) return;
    this.connecting = true; this.error = ''; this.changed();
    try {
      const browser = await chromium.connectOverCDP(this.endpoint, { timeout: 20000, noDefaults: true });
      if (epoch !== this.epoch) { await browser.close(); throw new Error('Chrome disconnected.'); }
      this.browser = browser;
      browser.on('disconnected', () => {
        if (this.browser !== browser) return;
        this.browser = null; this.page = null; this.elements = []; this.title = ''; this.changed();
      });
    } catch (error) {
      if (epoch !== this.epoch) throw error;
      throw new Error('Chrome needs a connection. Open Chrome settings below, enable “Allow remote debugging for this browser instance”, then try Connect Chrome again. Chrome may ask you to allow the connection.');
    } finally { if (epoch === this.epoch) { this.connecting = false; this.changed(); } }
  }
  async tab(epoch) {
    if (this.page && !this.page.isClosed()) return this.page;
    const page = await this.browser.contexts()[0].newPage();
    if (epoch !== this.epoch) throw new Error('Chrome disconnected.');
    this.page = page;
    page.setDefaultTimeout(10000);
    page.on('framenavigated', frame => {
      if (this.page !== page || frame !== page.mainFrame()) return;
      this.elements = []; this.title = ''; this.changed();
      page.title().then(title => { if (this.page === page) { this.title = title; this.changed(); } }).catch(() => {});
    });
    page.on('close', () => { if (this.page === page) { this.page = null; this.elements = []; this.title = ''; this.changed(); } });
    return page;
  }
  async disconnect() {
    ++this.epoch;
    this.paused = true; this.connecting = false; this.loading = false; this.error = '';
    const browser = this.browser;
    this.browser = null; this.page = null; this.elements = []; this.title = ''; this.changed();
    if (browser) await browser.close().catch(() => {});
    return this.state();
  }
  action(input) {
    if (!input || typeof input.action !== 'string') return Promise.reject(new Error('Choose a browser action.'));
    if (input.action === 'status') return Promise.resolve(this.state());
    if (input.action === 'disconnect') return this.disconnect();
    if (input.action === 'setup') return this.openChrome('chrome://inspect/#remote-debugging').then(() => this.state());
    const epoch = this.epoch;
    const run = this.queue.then(() => this.perform(input, epoch));
    this.queue = run.catch(() => {});
    return run;
  }
  async perform(input, epoch) {
    if (epoch !== this.epoch) throw new Error('Chrome disconnected.');
    if (!['connect', 'show', 'navigate', 'read', 'click', 'fill', 'back', 'scroll'].includes(input.action)) throw new Error('That browser action is not supported.');
    if (input.action === 'connect') this.paused = false;
    if (this.paused) throw new Error('Chrome is disconnected. Ask the user to click Connect Chrome when they want to resume.');
    this.error = ''; this.loading = true; this.changed();
    try {
      await this.connect(epoch);
      if (epoch !== this.epoch) throw new Error('Chrome disconnected.');
      if (input.action === 'connect') return this.state();
      const page = await this.tab(epoch);
      if (input.action === 'show') { await page.bringToFront(); await this.openChrome(); }
      else if (input.action === 'navigate') { await page.goto(webUrl(input.url), { waitUntil: 'domcontentloaded', timeout: 20000 }); }
      else {
        if (page.url() === 'about:blank') throw new Error('No website open yet. Navigate to a URL first.');
        webUrl(page.url());
        if (input.action === 'read') {
          for (const el of this.elements) await el.dispose().catch(() => {});
          this.elements = [];
          const candidates = await page.locator('a[href],button,input,textarea,select,[role="button"],[contenteditable="true"]').elementHandles();
          for (const el of candidates) {
            if (this.elements.length < 250 && await el.isVisible()) this.elements.push(el);
            else await el.dispose();
          }
          const elements = await Promise.all(this.elements.map((el, i) => el.evaluate((el, index) => ({ element: index + 1, tag: el.tagName.toLowerCase(), label: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') || '').slice(0, 180), type: el.getAttribute('type') || '', href: el.getAttribute('href') || '' }), i)));
          return { title: await page.title(), url: page.url(), text: (await page.locator('body').innerText()).slice(0, 18000), elements };
        }
        if (input.action === 'click' || input.action === 'fill') {
          const el = Number.isInteger(input.element) && this.elements[input.element - 1];
          if (!el) throw new Error('Read the page first, then choose an element number.');
          if (input.action === 'click') await el.click();
          else {
            if (typeof input.text !== 'string' || input.text.length > 20000) throw new Error('Enter text up to 20,000 characters.');
            await el.fill(input.text);
          }
        } else if (input.action === 'back') await page.goBack({ waitUntil: 'domcontentloaded', timeout: 20000 });
        else if (input.action === 'scroll') await page.evaluate(y => window.scrollBy(0, y), input.direction === 'up' ? -600 : 600);
      }
      this.title = await page.title();
      return this.state();
    } catch (error) {
      if (epoch === this.epoch) this.error = error.message;
      throw error;
    } finally { if (epoch === this.epoch) { this.loading = false; this.changed(); } }
  }
  close() { return this.disconnect(); }
}
module.exports = { KenBrowser };
