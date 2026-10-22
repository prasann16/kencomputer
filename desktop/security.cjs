// Native calls are accepted only from Ken's own page in the app window.
function trustedFrame(event, win, origin) {
  return !!win && !win.isDestroyed() && event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame && new URL(event.senderFrame.url).origin === origin;
}
function localComputerFrame(event, win, origin) {
  return trustedFrame(event, win, origin) && !new URL(event.senderFrame.url).pathname.startsWith('/computer/');
}
module.exports = { trustedFrame, localComputerFrame };
