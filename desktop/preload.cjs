const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('kenDesktop', {
  platform: process.platform,
  microphonePermission: () => ipcRenderer.invoke('microphone:permission'),
  microphoneSettings: (kind) => ipcRenderer.invoke('microphone:settings', kind),
  pickFolder: () => ipcRenderer.invoke('folder:pick'),
  openFolder: (folder) => ipcRenderer.invoke('folder:open', folder),
  popupMenu: (items) => ipcRenderer.invoke('menu:popup', items),
  connectChrome: () => ipcRenderer.invoke('chrome:connect'),
  botTokenFromClipboard: () => ipcRenderer.invoke('clipboard:bot-token'),
  updateFailed: () => ipcRenderer.invoke('update:failed'),
  updateStatus: () => ipcRenderer.invoke('update:status'),
  checkForUpdates: () => ipcRenderer.invoke('update:check'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  onUpdateState: (callback) => { const listener = (_event, state) => callback(state); ipcRenderer.on('update:state', listener); return () => ipcRenderer.removeListener('update:state', listener); },
});
