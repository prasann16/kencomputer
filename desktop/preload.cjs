const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('kenDesktop', {
  platform: process.platform,
  microphonePermission: () => ipcRenderer.invoke('microphone:permission'),
  microphoneSettings: (kind) => ipcRenderer.invoke('microphone:settings', kind),
  onUpdateState: (callback) => { const listener = (_event, state) => callback(state); ipcRenderer.on('update:state', listener); return () => ipcRenderer.removeListener('update:state', listener); },
});
