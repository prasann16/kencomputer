// Native calls are accepted only from Ken's own page in the app window.
function trustedFrame(event, win, origin) {
  return !!win && !win.isDestroyed() && event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame && new URL(event.senderFrame.url).origin === origin;
}
module.exports = { trustedFrame };
