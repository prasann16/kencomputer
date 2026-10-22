const { CancellationToken, parseXml, newError } = require('builder-util-runtime');
const semver = require('semver');
const { GitHubProvider, computeReleaseNotes } = require('electron-updater/out/providers/GitHubProvider');
const { parseUpdateInfo } = require('electron-updater/out/providers/Provider');
const { acceptsVersion } = require('./updates.cjs');

class GitHubUpdates extends GitHubProvider {
  constructor(options, updater, runtimeOptions) {
    super(options, updater, runtimeOptions);
    this.kenUpdater = updater;
  }

  async getLatestVersion() {
    if (!this.kenUpdater.allowPrerelease) return super.getLatestVersion();

    // GitHub orders this feed by date. A weekly stable release can appear before
    // a higher-version beta, so beta checks must compare every eligible entry.
    const token = new CancellationToken();
    const basePath = `/${this.options.owner}/${this.options.repo}/releases`;
    const feed = parseXml(await this.httpRequest(new URL(`${basePath}.atom`, this.baseUrl), {
      accept: 'application/atom+xml',
    }, token));
    let latest, tag;
    for (const entry of feed.getElements('entry')) {
      const href = entry.elementOrNull('link')?.attributes?.href || '';
      const candidate = /\/tag\/(v?[^/]+)$/.exec(href)?.[1];
      if (!candidate || !semver.valid(candidate) || !acceptsVersion('beta', candidate.replace(/^v/, ''))) continue;
      if (!tag || semver.gt(candidate, tag)) { latest = entry; tag = candidate; }
    }
    if (!tag) throw newError('No published stable or beta versions on GitHub', 'ERR_UPDATER_NO_PUBLISHED_VERSIONS');

    const channel = semver.prerelease(tag) ? 'beta' : 'latest';
    const filename = `${this.getCustomChannelName(channel)}.yml`;
    const url = new URL(`${basePath}/download/${tag}/${filename}`, this.baseUrl);
    const info = parseUpdateInfo(await this.httpRequest(url, null, token), filename, url);
    return {
      ...info,
      tag,
      releaseName: info.releaseName ?? latest.elementValueOrEmpty('title'),
      releaseNotes: info.releaseNotes ?? computeReleaseNotes(this.kenUpdater.currentVersion, this.kenUpdater.fullChangelog, feed, latest),
    };
  }
}

module.exports = { GitHubUpdates };
