const { app, BrowserWindow, Menu, shell, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path   = require('path');
const http   = require('http');
const net    = require('net');
const fs     = require('fs');
const os     = require('os');
const discordPresence = require('./discord-presence');
const { initAutoUpdate } = require('./updater');
const { migratePartition } = require('./migrate');

// All per-user state lives under <appData>/Lemur on every OS. Set before
// 'ready' so Chromium's profile lands there, and pass the same folder to the
// backend (LEMUR_DATA_DIR) so the two can never disagree.
app.setName('Lemur');
const USER_DATA = path.join(app.getPath('appData'), 'Lemur');
app.setPath('userData', USER_DATA);
const PARTITION = 'persist:lemur';

// Disable GPU hardware acceleration — required on many Windows systems
// where Chromium's GPU process crashes with the system's graphics driver.
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-software-rasterizer');
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-gpu-sandbox');
app.disableHardwareAcceleration();

// Log file for debugging startup issues. When packaged, __dirname lives
// inside the read-only app.asar, so the log must go to a writable location —
// otherwise this write throws at module load and the app never starts.
const LOG_FILE = app.isPackaged
  ? path.join(os.tmpdir(), 'lemur-electron.log')
  : path.join(__dirname, '..', 'electron.log');
try { fs.writeFileSync(LOG_FILE, ''); } catch (_) {}   // clear on each launch
function log(...args) {
  const line = `[${new Date().toISOString()}] ${args.join(' ')}\n`;
  process.stdout.write(line);
  try { fs.appendFileSync(LOG_FILE, line); } catch(_) {}
}

// Route Discord presence diagnostics into the same log file.
try { discordPresence.setLogger(log); } catch (_) {}

/* ─────────────────────────────────────────────────────────────
   Config
───────────────────────────────────────────────────────────── */
const FLASK_HOST  = '127.0.0.1';          // explicit IPv4 — avoids IPv6 issues on Windows 11
// Port is chosen dynamically at startup (see pickFreePort) so we never collide
// with another process holding a fixed port. Set once before Flask spawns.
let   FLASK_PORT  = 5000;
let   FLASK_URL   = `http://${FLASK_HOST}:${FLASK_PORT}`;
const POLL_MS     = 300;
const TIMEOUT_MS  = 60000;   // first-ever cold PyInstaller start can be slow

/* ─────────────────────────────────────────────────────────────
   State
───────────────────────────────────────────────────────────── */
let mainWindow   = null;
let splashWindow = null;
let flaskProcess = null;
let backendReady = false;
let portAssigned = false;   // true once startFlask() locks in the real port

// Inline the lemur logo as base64 so the splash can show it before the backend
// serves /static. Resolved from extraResources when packaged, static/ in dev.
function logoDataUri() {
  const p = app.isPackaged
    ? path.join(process.resourcesPath, 'lemurtext.png')
    : path.join(__dirname, '..', 'static', 'lemurtext.png');
  try { return 'data:image/png;base64,' + fs.readFileSync(p).toString('base64'); }
  catch (_) { return ''; }
}

// Self-contained splash document (logo on brand-dark + a subtle progress bar).
// Shown in its OWN short-lived window — never navigated — so it cannot affect
// the main window's frameless drag regions.
function splashHTML() {
  const logo = logoDataUri();
  return 'data:text/html,' + encodeURIComponent(`
<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{margin:0;height:100%;background:#0e0e0f;overflow:hidden}
  .wrap{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px}
  img{width:200px;object-fit:contain;animation:p 2.4s ease-in-out infinite}
  @keyframes p{0%,100%{transform:translateY(0) scale(1);opacity:1}50%{transform:translateY(-4px) scale(1.015);opacity:.92}}
  .bar{width:180px;height:4px;border-radius:3px;background:#2a2a2e;overflow:hidden}
  .fill{width:40%;height:100%;border-radius:3px;background:#7c6cff;animation:s 1.1s ease-in-out infinite}
  @keyframes s{0%{transform:translateX(-120%)}100%{transform:translateX(360%)}}
</style></head><body><div class="wrap">
  ${logo ? `<img src="${logo}" alt="Lemur">` : ''}
  <div class="bar"><div class="fill"></div></div>
</div></body></html>`);
}

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 420, height: 320, frame: false, resizable: false,
    show: false, backgroundColor: '#0e0e0f', center: true,
    skipTaskbar: true, webPreferences: { offscreen: false },
  });
  splashWindow.loadURL(splashHTML()).catch(() => {});
  splashWindow.once('ready-to-show', () => { if (splashWindow) splashWindow.show(); });
  splashWindow.on('closed', () => { splashWindow = null; });
}

function closeSplash() {
  if (splashWindow) { try { splashWindow.close(); } catch (_) {} splashWindow = null; }
}

/* ─────────────────────────────────────────────────────────────
   Start Flask server
───────────────────────────────────────────────────────────── */
// Poll the lightweight /healthz endpoint until Flask is serving. Polls are
// SEQUENTIAL (each next attempt is scheduled only after the previous finishes),
// so a slow cold start can't pile up overlapping requests and starve itself —
// the bug that made the strict full-page poll time out on slower Macs. The
// blank-screen guard now lives in the render-verify watchdog after load, so
// here we just need to know the server is up.
function pollUntilReady(resolve, reject) {
  const deadline = Date.now() + TIMEOUT_MS;

  function attempt() {
    if (Date.now() > deadline) {
      reject(new Error('Backend did not respond within timeout.'));
      return;
    }
    const req = http.get(
      { host: FLASK_HOST, port: FLASK_PORT, path: '/healthz', timeout: 2500 },
      res => {
        res.resume();   // drain
        if (res.statusCode === 200) {
          log('Backend ready — /healthz 200');
          resolve();
        } else {
          setTimeout(attempt, POLL_MS);
        }
      });
    req.on('error',   () => setTimeout(attempt, POLL_MS));
    req.on('timeout', () => { req.destroy(); setTimeout(attempt, POLL_MS); });
    req.end();
  }
  attempt();
}

function wireProcessLogging(proc, label) {
  proc.stdout.on('data', d => log(`[${label}]`, d.toString().trim()));
  proc.stderr.on('data', d => log(`[${label}]`, d.toString().trim()));
  proc.on('exit', code => log(`${label} exited with code ${code}`));
}

// Ask the OS for a free TCP port on the loopback interface. We bind to port 0
// (the OS assigns an unused port), read it back, then release it — and hand
// that number to Flask. This sidesteps fixed-port collisions entirely: we
// never fight over 5000, so a stale/orphaned process on it can't block us.
function pickFreePort(preferred) {
  return new Promise(resolve => {
    // Try the preferred port first (keeps URLs stable across normal launches).
    const tryPort = (p, fallbackToAny) => {
      const srv = net.createServer();
      srv.once('error', () => {
        if (fallbackToAny) tryPort(0, false);   // preferred taken → let OS choose
        else resolve(null);
      });
      srv.listen(p, FLASK_HOST, () => {
        const chosen = srv.address().port;
        srv.close(() => resolve(chosen));
      });
    };
    tryPort(preferred || 0, true);
  });
}

function startFlask() {
  return new Promise(async (resolve, reject) => {
    const projectRoot = path.join(__dirname, '..');

    // Choose a free port up front and hand it to Flask via LEMUR_PORT. We
    // prefer 5000 (stable across normal launches) but fall back to any OS-
    // assigned free port if it's taken — so a stale/orphaned process on 5000,
    // or any other conflict, can never block startup.
    const port = await pickFreePort(5000);
    if (!port) {
      const msg = `Couldn't reserve a local port for the Lemur backend.\n\n` +
        `A firewall or security tool may be blocking local connections. Try ` +
        `relaunching, or reboot if it persists.`;
      log('STARTUP ABORTED: ' + msg.replace(/\n/g, ' '));
      reject(new Error(msg));
      return;
    }
    FLASK_PORT = port;
    FLASK_URL  = `http://${FLASK_HOST}:${FLASK_PORT}`;
    portAssigned = true;   // window may now load FLASK_URL (the real port)
    log(`Using backend port ${FLASK_PORT}`);
    const backendEnv = { LEMUR_ELECTRON: '1', PYTHONUNBUFFERED: '1', LEMUR_PORT: String(FLASK_PORT), LEMUR_DATA_DIR: USER_DATA };

    // ── Production: spawn the frozen PyInstaller backend ──────────────────
    // electron-builder ships the one-folder build under
    // resources/lemur-server/. No Python needed on the user's machine.
    if (app.isPackaged) {
      const exeName = process.platform === 'win32' ? 'lemur-server.exe' : 'lemur-server';
      const exePath = path.join(process.resourcesPath, 'lemur-server', exeName);
      log(`Spawning frozen backend: ${exePath}`);

      const proc = spawn(exePath, [], {
        cwd: path.dirname(exePath),
        env: { ...process.env, ...backendEnv },
        windowsHide: true,
      });

      proc.on('error', err => {
        log(`Frozen backend spawn error: ${err.message}`);
        reject(new Error(`Failed to start the Lemur backend.\n${exePath}\n${err.message}`));
      });

      flaskProcess = proc;
      wireProcessLogging(proc, 'Backend');
      pollUntilReady(resolve, reject);
      return;
    }

    // ── Development: run app.py with whatever Python is on PATH ────────────
    const pythonCandidates = process.platform === 'win32'
      ? ['python', 'python3', 'py']
      : ['python3', 'python'];

    function tryNext(candidates) {
      if (!candidates.length) {
        reject(new Error('Python not found. Make sure Python 3 is installed and in PATH.'));
        return;
      }
      const cmd = candidates[0];
      const rest = candidates.slice(1);
      log(`Trying: ${cmd} app.py  (cwd: ${projectRoot})`);

      const proc = spawn(cmd, ['app.py'], {
        cwd: projectRoot,
        env: { ...process.env, ...backendEnv },
        windowsHide: true,
        shell: true,         // required for Windows Store Python execution aliases
      });

      proc.on('error', (err) => {
        log(`spawn error for '${cmd}': ${err.message}`);
        tryNext(rest);
      });

      proc.stderr.once('data', (d) => {
        log(`Flask started with '${cmd}': ${d.toString().trim()}`);
        flaskProcess = proc;
        wireProcessLogging(proc, 'Flask');
        pollUntilReady(resolve, reject);
      });

      proc.on('exit', () => { if (!flaskProcess) tryNext(rest); });
    }

    tryNext(pythonCandidates);
  });
}

/* ─────────────────────────────────────────────────────────────
   Window helpers
───────────────────────────────────────────────────────────── */
// Resolve the Windows window/taskbar icon. On macOS the icon comes from the
// .icns inside the .app bundle, so BrowserWindow needs no explicit icon there
// — return undefined and let Electron use the bundle icon.
// Packaged (Windows): shipped unpacked via extraResources. Dev: build/ folder.
function appIconPath() {
  if (process.platform === 'darwin') return undefined;
  return app.isPackaged
    ? path.join(process.resourcesPath, 'icon.ico')
    : path.join(__dirname, '..', 'build', 'icon.ico');
}

function panelWindowOptions(extra = {}) {
  return {
    width:  900,
    height: 700,
    minWidth:  380,
    minHeight: 300,
    frame: false,             // custom in-page title bar (matches pywebview)
    icon: appIconPath(),
    backgroundColor: '#0e0e0f',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      partition: PARTITION,
      preload: path.join(__dirname, 'preload.js'),
    },
    ...extra,
  };
}

function createMainWindow() {
  log('Creating BrowserWindow...');
  mainWindow = new BrowserWindow({
    width:  1400,
    height: 900,
    minWidth:  900,
    minHeight: 600,
    title: 'Lemur',
    icon: appIconPath(),
    frame: false,            // custom in-page title bar (the #topbar window controls)
    show: false,             // kept hidden until the page is confirmed rendered (see showWindow)
    backgroundColor: '#0e0e0f',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      partition: PARTITION,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  Menu.setApplicationMenu(null);

  // The main window stays HIDDEN until the real Flask page has rendered; the
  // separate splash window covers the wait. We reveal the main window and close
  // the splash together in finalizeWindow(), so the user never sees the main
  // window's empty/dark state — only the branded splash, then the app.
  let _finalized = false;
  function finalizeWindow() {
    if (_finalized || !mainWindow) return;
    _finalized = true;
    log('Finalizing window — show + maximize, close splash');
    mainWindow.show();
    // Maximizing resizes the window, which makes Chromium recompute the
    // frameless window's -webkit-app-region from the now-rendered real DOM so
    // dragging the #topbar works.
    mainWindow.maximize();
    mainWindow.focus();
    closeSplash();
  }

  // ── Blank-screen recovery ──────────────────────────────────────────────
  // Symptom (mainly macOS, cold start): the window shows but the page is blank
  // until you quit and relaunch. Causes: a hard load failure, OR a "successful"
  // load that didn't actually render (a stylesheet/script/asset raced in late).
  // We auto-reload a limited number of times instead of leaving a black screen.
  let _reloadAttempts = 0;
  const MAX_RELOADS = 20;   // ~20s of coverage at 1s spacing for a slow cold start

  function reloadOnce(reason) {
    if (!mainWindow) return;
    if (_reloadAttempts >= MAX_RELOADS) {
      log(`Giving up after ${MAX_RELOADS} reloads (${reason})`);
      // Don't leave the user stuck on the splash forever — reveal whatever the
      // main window has so they at least see the app (or its error state).
      finalizeWindow();
      return;
    }
    _reloadAttempts++;
    log(`Blank/failed page (${reason}) — reload attempt ${_reloadAttempts}/${MAX_RELOADS}`);
    setTimeout(() => {
      if (mainWindow) mainWindow.loadURL(FLASK_URL).catch(e => log('retry error:', e.message));
    }, 1000);
  }

  // Capture renderer console output (esp. uncaught errors) into electron.log,
  // so a JS error that silently kills the page is diagnosable without DevTools.
  mainWindow.webContents.on('console-message', (event, level, message, line, sourceId) => {
    // level: 0=log 1=warning 2=error 3=info
    if (level >= 2) {
      const src = (sourceId || '').split('/').pop();
      log(`[renderer console:${level}] ${message}  (${src}:${line})`);
    }
  });
  // Also surface preload/uncaught exceptions in the renderer.
  mainWindow.webContents.on('preload-error', (event, preloadPath, err) => {
    log(`[preload error] ${preloadPath}: ${err && err.message}`);
  });

  // True once we've issued the first real app load. The render-watchdog applies
  // from then on.
  let _appLoadStarted = false;

  // Load the Flask app page. We do this as soon as the backend's port is known
  // (FLASK_URL set) WITHOUT waiting for full readiness: the page's own in-app
  // loading screen (logo + fact) covers the warm-up, and if the very first
  // request races ahead of Flask, did-fail-load triggers the watchdog to retry
  // every second until it succeeds. This avoids a black empty window during a
  // slow cold start (no separate loading document → no drag-region corruption).
  function loadApp() {
    if (!mainWindow) return;
    _appLoadStarted = true;
    log(`Loading app URL: ${FLASK_URL}`);
    mainWindow.loadURL(FLASK_URL).catch(err => log('loadURL error:', err.message));
  }

  // Decide when to issue the first load:
  //  • Prefer waiting until the backend is READY, so the very first request
  //    succeeds and the page's in-app loading screen shows immediately (no
  //    white error-page flash from a refused connection).
  //  • But cap that wait (3s after the port is known): if the backend is slow,
  //    load anyway and let the render-watchdog retry — better a brief flash
  //    than a long blank window.
  let _waitedMs = 0;
  function loadAppWhenPortReady() {
    if (!mainWindow || _appLoadStarted) return;
    if (!portAssigned) { setTimeout(loadAppWhenPortReady, 50); return; }
    if (backendReady || _waitedMs >= 3000) { loadApp(); return; }
    _waitedMs += 100;
    setTimeout(loadAppWhenPortReady, 100);
  }

  mainWindow.webContents.on('did-fail-load', (event, code, desc, url, isMainFrame) => {
    if (!isMainFrame || !_appLoadStarted) return;   // ignore loading-screen + sub-resources
    log(`Page load failed: ${code} ${desc} — ${url}`);
    reloadOnce('did-fail-load');
  });

  mainWindow.webContents.on('render-process-gone', (event, details) => {
    log(`Renderer crashed: ${details.reason} (exit code ${details.exitCode})`);
    if (_appLoadStarted) reloadOnce('render-process-gone');
  });

  mainWindow.webContents.on('did-finish-load', () => {
    if (!_appLoadStarted) return;        // the loading screen finished — ignore
    // Verify the real page actually rendered. If #app is missing or zero-size,
    // the load "succeeded" but produced a blank screen — reload.
    mainWindow.webContents.executeJavaScript(`(() => {
      const a = document.getElementById('app');
      return !!(a && a.getBoundingClientRect().height > 0);
    })()`).then(rendered => {
      if (rendered) {
        log('App loaded and rendered successfully');
        _reloadAttempts = 0;
        finalizeWindow();   // maximize → recompute topbar drag region
      } else {
        reloadOnce('empty #app after load');
      }
    }).catch(err => {
      log(`render check error: ${err.message}`);
      reloadOnce('render check threw');
    });
  });

  mainWindow.on('closed', () => {
    log('Main window closed');
    mainWindow = null;
  });

  // The main window stays hidden; the splash covers the wait. Load the Flask
  // app (the ONE AND ONLY document this window ever loads) once the port is
  // known, preferring to wait for readiness so the first request succeeds.
  // finalizeWindow() reveals the window + closes the splash once it renders.
  loadAppWhenPortReady();

  // Intercept window.open() — each panel becomes its own BrowserWindow
  mainWindow.webContents.setWindowOpenHandler(({ url, features }) => {
    // External web/mail links (Ctrl+click on an embedded hyperlink) must open
    // in the user's real browser, never inside an app window. Our own panels
    // are localhost URLs, so anything non-localhost is treated as external.
    try {
      const u = new URL(url);
      const isLocal = u.hostname === 'localhost' || u.hostname === '127.0.0.1';
      if (u.protocol === 'mailto:' ||
          ((u.protocol === 'http:' || u.protocol === 'https:') && !isLocal)) {
        shell.openExternal(url);
        return { action: 'deny' };
      }
    } catch (_) { /* not a parseable URL — fall through to panel handling */ }
    const fw = parseInt((features.match(/width=(\d+)/)  || [])[1]) || 900;
    const fh = parseInt((features.match(/height=(\d+)/) || [])[1]) || 700;
    const panelParam = (() => { try { return new URL(url).searchParams.get('panel') || 'panel'; } catch(_) { return 'panel'; } })();
    const titles = { illus: 'Illustration', sidebar: 'Sidebar', reader: 'Reader' };
    log(`Opening panel window: ${url}`);
    return {
      action: 'allow',
      overrideBrowserWindowOptions: panelWindowOptions({
        width: fw, height: fh,
        title: `Lemur — ${titles[panelParam] || 'Panel'}`,
      }),
    };
  });
}

/* ─────────────────────────────────────────────────────────────
   IPC — native API for the renderer (window controls + folder picker)
───────────────────────────────────────────────────────────── */
// Window controls for the frameless windows. Acts on whichever window sent
// the message. Returns the new maximized state (only used for 'togglemax').
ipcMain.handle('window-cmd', (event, cmd) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return false;
  switch (cmd) {
    case 'minimize':  win.minimize(); return false;
    case 'close':     win.close();    return false;
    case 'togglemax':
      if (win.isMaximized()) { win.unmaximize(); return false; }
      win.maximize(); return true;
    default: return false;
  }
});

// Native folder picker for the library's "Add folder" button.
ipcMain.handle('pick-folder', async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  const res = await dialog.showOpenDialog(win, { properties: ['openDirectory'] });
  if (res.canceled || !res.filePaths.length) return null;
  return res.filePaths[0];
});

// Reveal the data folder in Explorer/Finder. openPath opens the folder itself.
ipcMain.handle('open-path', async (event, p) => {
  if (!p) return false;
  try { const err = await shell.openPath(String(p)); return !err; }
  catch (_) { return false; }
});

// ── Discord Rich Presence ────────────────────────────────────────────────
// The renderer flips this on/off (Settings toggle) and pushes reading state.
ipcMain.handle('discord-enabled', (event, on) => {
  try { discordPresence.setEnabled(on); } catch (_) {}
  return true;
});
ipcMain.on('discord-activity', (event, activity) => {
  try { discordPresence.setActivity(activity); } catch (_) {}
});

/* ─────────────────────────────────────────────────────────────
   App lifecycle
───────────────────────────────────────────────────────────── */
log('main.js loaded');

app.on('render-process-gone', (event, wc, details) => {
  log(`Global render-process-gone: ${details.reason}`);
});

app.whenReady().then(() => {
  log('Electron app ready');
  // Carry UI preferences over from the pre-Lemur storage partition. Must run
  // before any window opens: opening one creates the new partition folder.
  try { migratePartition({ userData: USER_DATA, appData: app.getPath('appData'), log }); }
  catch (e) { log('[migrate] skipped: ' + e.message); }
  // Show the branded splash window INSTANTLY (its own short-lived window — never
  // navigated, so it can't affect the main window's drag regions). The main
  // window is created hidden and revealed only once Flask has rendered.
  createSplash();
  createMainWindow();

  // Auto-update: check the public releases channel in the background and offer
  // to install when a newer version is downloaded. No-op in dev builds.
  try { initAutoUpdate({ app, getWindow: () => mainWindow, log }); }
  catch (e) { log('[updater] init failed: ' + e.message); }

  // Start the backend in parallel. The window loads as soon as the port is
  // assigned (synchronously, early in startFlask); surface a Retry/Quit dialog
  // only if the backend process genuinely failed to start.
  log('Starting backend…');
  startFlask().then(() => {
    log('Backend ready');
    backendReady = true;
  }).catch(err => {
    log('STARTUP WARNING: ' + err.message);
    if (flaskProcess && !flaskProcess.killed) {
      // Slow but alive — let the loading screen keep waiting a bit longer.
      backendReady = true;   // attempt the load; render-watchdog handles retries
    } else {
      log('STARTUP ERROR: backend process not running');
      closeSplash();   // don't leave the splash hanging behind the error dialog
      const choice = dialog.showMessageBoxSync({
        type: 'error',
        title: 'Lemur — Startup Error',
        message: "Lemur's backend didn't start.",
        detail: err.message + '\n\nThis is usually temporary. Try again?',
        buttons: ['Retry', 'Quit'],
        defaultId: 0,
        cancelId: 1,
      });
      if (choice === 0) app.relaunch();
      app.quit();
    }
  });
});

app.on('window-all-closed', () => {
  log('All windows closed');
  if (flaskProcess) {
    try { flaskProcess.kill(); } catch (_) {}
    flaskProcess = null;
  }
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (flaskProcess) {
    try { flaskProcess.kill(); } catch (_) {}
  }
});

app.on('activate', () => {
  if (mainWindow === null) createMainWindow();
});
