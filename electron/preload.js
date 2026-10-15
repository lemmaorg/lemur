// Preload bridge — safely exposes a tiny native API to the renderer.
// The frontend was originally written for pywebview (window.pywebview.api);
// this provides the Electron equivalent under window.electronAPI so the
// custom frameless window controls and the native folder picker work.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // cmd: 'minimize' | 'togglemax' | 'close'. Resolves to the new maximized
  // state (only meaningful for 'togglemax').
  windowCmd: (cmd) => ipcRenderer.invoke('window-cmd', cmd),
  // Opens a native folder picker. Resolves to the chosen path, or null.
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  // Reveals a folder/file in the OS file manager. Resolves true/false.
  openPath: (p) => ipcRenderer.invoke('open-path', p),

  // ── Discord Rich Presence ──
  // Turn presence on/off (Settings toggle).
  discordSetEnabled: (on) => ipcRenderer.invoke('discord-enabled', on),
  // Push current reading state: { title, percent }. Fire-and-forget.
  discordSetActivity: (activity) => ipcRenderer.send('discord-activity', activity),
});
