function webUrl(value) {
  if (typeof value !== 'string' || value.length > 4096) throw new Error('Enter a valid website address.');
  const input = value.trim();
  const url = new URL(/^[a-z][a-z\d+.-]*:/i.test(input) ? input : 'https://' + input);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('Only http and https websites can be opened.');
  const host = url.hostname.toLowerCase();
  if (host === 'localhost' || host.endsWith('.localhost') || host.startsWith('127.') || host === '[::1]' || host === '0.0.0.0') throw new Error('Local app addresses cannot be opened by Ken’s browser tool.');
  return url.href;
}
function trustedFrame(event, win, origin) {
  return !!win && !win.isDestroyed() && event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame && new URL(event.senderFrame.url).origin === origin;
}
module.exports = { webUrl, trustedFrame };
