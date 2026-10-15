/**
 * Auto-update for Lemur (electron-updater).
 *
 * Checks the public releases channel (configured as `publish` in
 * electron-builder.config.js) for a newer version, downloads it in the
 * background, and offers to restart-and-install. If the user dismisses the
 * prompt, the update is applied automatically on the next quit.
 *
 * Platform reality:
 *   - Windows: works on unsigned NSIS builds — live today.
 *   - macOS:   Squirrel.Mac only APPLIES updates to a signed + notarized app.
 *     On an unsigned Mac build checkForUpdates() fails; we just log it and do
 *     nothing (no error dialogs). Once the Mac build is signed, this same code
 *     starts applying Mac updates with no changes.
 *
 * Dev builds (not packaged) have no update metadata, so auto-update is skipped.
 */
const { autoUpdater } = require('electron-updater');
const { dialog } = require('electron');

let _log = () => {};
let _getWindow = () => null;
let _timer = null;

// Re-check periodically while the app stays open (long-running sessions).
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;   // 6 hours
const FIRST_CHECK_DELAY  = 8000;                // let the app settle after launch

function initAutoUpdate({ app, getWindow, log } = {}) {
  if (log) _log = (m) => log(m);
  if (getWindow) _getWindow = getWindow;

  if (!app || !app.isPackaged) {
    _log('[updater] dev build — auto-update disabled');
    return;
  }

  // Route electron-updater's own logging into our log file.
  autoUpdater.logger = {
    info:  (m) => _log('[updater] ' + m),
    warn:  (m) => _log('[updater] WARN ' + m),
    error: (m) => _log('[updater] ERROR ' + m),
    debug: () => {},
  };
  autoUpdater.autoDownload = true;             // fetch the update in the background
  autoUpdater.autoInstallOnAppQuit = true;     // fallback: apply silently on quit

  autoUpdater.on('checking-for-update', () => _log('[updater] checking for update'));
  autoUpdater.on('update-available',     (i) => _log('[updater] update available: ' + (i && i.version)));
  autoUpdater.on('update-not-available', ()  => _log('[updater] up to date'));
  autoUpdater.on('download-progress',    (p) => _log('[updater] downloading ' + Math.round(p.percent) + '%'));
  autoUpdater.on('error', (err) => _log('[updater] error: ' + (err && err.message ? err.message : String(err))));
  autoUpdater.on('update-downloaded', (info) => {
    _log('[updater] update downloaded: ' + (info && info.version));
    promptRestart(info);
  });

  setTimeout(safeCheck, FIRST_CHECK_DELAY);
  _timer = setInterval(safeCheck, CHECK_INTERVAL_MS);
}

// Wrapped so a thrown error (e.g. unsigned macOS, offline, channel not yet
// created) never escapes — it's logged via the 'error' event / catch.
function safeCheck() {
  try { autoUpdater.checkForUpdates(); }
  catch (e) { _log('[updater] check failed: ' + (e && e.message)); }
}

function promptRestart(info) {
  const win = _getWindow();
  const opts = {
    type: 'info',
    buttons: ['Restart now', 'Later'],
    defaultId: 0,
    cancelId: 1,
    title: 'Update ready',
    message: `Lemur ${info && info.version ? info.version : ''} has been downloaded.`.trim(),
    detail: 'Restart now to install it, or it will be applied automatically the next time you quit Lemur.',
  };
  const onChoice = (result) => {
    const response = typeof result === 'number' ? result : (result && result.response);
    if (response === 0) {
      // Defer so the dialog fully closes before the app tears down.
      setImmediate(() => {
        try { autoUpdater.quitAndInstall(); }
        catch (e) { _log('[updater] quitAndInstall failed: ' + (e && e.message)); }
      });
    }
  };
  (win ? dialog.showMessageBox(win, opts) : dialog.showMessageBox(opts)).then(onChoice);
}

module.exports = { initAutoUpdate };
