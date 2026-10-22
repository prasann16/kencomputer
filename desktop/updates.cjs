// Keep channel changes and downloads in order, so a stale beta cannot install after opting out.
const validChannel = (channel) => channel === 'stable' || channel === 'beta';
const acceptsVersion = (channel, version) => /^\d+\.\d+\.\d+$/.test(version) ||
  (channel === 'beta' && /^\d+\.\d+\.\d+-beta\.[1-9]\d*$/.test(version));
const initialChannel = (saved, version) => validChannel(saved) ? saved : /-beta\./.test(version) ? 'beta' : 'stable';

class UpdateChecks {
  constructor(updater, channel, notify, { testFeed = false } = {}) {
    this.updater = updater;
    this.channel = channel;
    this.notify = notify;
    this.testFeed = testFeed;
    this.generation = 0;
    this.activeGeneration = 0;
    this.pending = null;
    this.token = null;
    this.manual = false;
    this.recheck = false;
    this.configure();
    const current = () => this.activeGeneration === this.generation;
    updater.on('update-available', (info) => {
      if (current() && this.manual && acceptsVersion(this.channel, info.version)) notify({ state: 'downloading', version: info.version });
    });
    updater.on('update-not-available', () => {
      if (!current()) return;
      if (this.manual) notify({ state: 'none' });
      this.manual = false;
    });
    updater.on('update-downloaded', (info) => {
      if (!current() || !acceptsVersion(this.channel, info.version)) return;
      notify({ state: 'ready', version: info.version });
      this.manual = false;
    });
    updater.on('error', (error) => {
      if (!current()) return;
      if (this.manual) notify({ state: 'error' });
      this.manual = false;
      console.error('update check failed:', error.message);
    });
  }

  configure() {
    this.updater.channel = this.testFeed || this.channel === 'stable' ? 'latest' : 'beta';
    this.updater.allowPrerelease = this.channel === 'beta';
    // Setting channel enables downgrades in electron-updater; never roll back user data automatically.
    this.updater.allowDowngrade = false;
    // Ken owns idle checks and engine shutdown before every install.
    this.updater.autoInstallOnAppQuit = false;
  }

  select(channel) {
    if (!validChannel(channel)) throw new Error('Invalid update channel.');
    if (channel === this.channel) return;
    this.channel = channel;
    this.generation++;
    this.configure();
    this.notify({ state: 'channel', channel });
    this.token?.cancel();
    this.check(true);
  }

  check(manual = false) {
    this.manual ||= manual;
    if (this.pending) {
      this.recheck ||= manual;
      return this.pending;
    }
    const generation = this.generation;
    this.activeGeneration = generation;
    this.pending = (async () => {
      const result = await this.updater.checkForUpdates();
      if (result) {
        this.token = result.cancellationToken;
        if (generation !== this.generation) this.token?.cancel();
        await result.downloadPromise;
      }
    })().catch(() => {}).finally(() => {
      this.pending = null;
      this.token = null;
      if (this.recheck) {
        this.recheck = false;
        this.check(this.manual);
      }
    });
    return this.pending;
  }
}

module.exports = { UpdateChecks, validChannel, acceptsVersion, initialChannel };
