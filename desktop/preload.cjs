const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('kenDesktop', {
  platform: process.platform,
  microphonePermission: () => ipcRenderer.invoke('microphone:permission'),
  microphoneSettings: (kind) => ipcRenderer.invoke('microphone:settings', kind),
});
