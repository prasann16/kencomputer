const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('kenDesktop', {
  platform: process.platform,
  microphonePermission: () => ipcRenderer.invoke('microphone:permission'),
  microphoneSettings: (kind) => ipcRenderer.invoke('microphone:settings', kind),
  accountAction: (action) => ipcRenderer.invoke('account:action', action),
  browserAction: (action) => ipcRenderer.invoke('browser:action', action),
  onBrowserState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('browser:state', listener);
    return () => ipcRenderer.removeListener('browser:state', listener);
  },
});
