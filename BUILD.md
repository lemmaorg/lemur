# Building Lemur

Lemur ships as an Electron app that spawns a **frozen Python backend**
(PyInstaller), so end users need neither Python nor Node installed.

```
Electron (Lemur)  →  resources/lemur-server/lemur-server[.exe]  →  Flask  →  UI
```

Official installers for Windows and macOS (Apple Silicon) are built by GitHub
Actions — see [Releases](#releases). You only need a local build to test
packaging changes.

---

## Run from source (development)

```bash
pip install -r requirements.txt   # Python 3.12 recommended
npm install
npm start                         # Electron + `python app.py`
```

Or run just the backend and open it in a browser: `python app.py`, then visit
http://127.0.0.1:5000.

## Tests

```bash
npm test          # Node tests, then Python tests
```

Python tests use only the standard library (`unittest`) plus the app's own
dependencies. They redirect every per-user folder to a temporary directory, so
they never touch real Lemur data.

---

## Local installer build

1. **A clean Python environment** with only the backend's dependencies and
   PyInstaller, and with it activated so `pyinstaller` is on your PATH:
   ```bash
   python -m venv .venv-build
   # Windows: .venv-build\Scripts\activate    macOS/Linux: source .venv-build/bin/activate
   pip install -r requirements.txt pyinstaller
   ```
   The environment must NOT contain `anthropic`, `pytesseract` or `pillow` —
   their absence keeps the dropped AI/OCR features out of the bundle.

2. **Build:**
   ```bash
   npm run dist        # = npm run build:backend && npm run build:installer
   ```

| Step | Script | Output |
|------|--------|--------|
| Freeze backend | `build:backend` (PyInstaller via `build-backend.mjs`) | `<root>/dist/lemur-server/` |
| Package + installer | `build:installer` (electron-builder) | `<root>/installer/` |

`<root>` is `LEMUR_BUILD_ROOT` (default `C:/Lemur-build`). Heavy build I/O is
kept outside the source tree on purpose: PyInstaller writes thousands of temp
files, and a synced folder (OneDrive, iCloud, Dropbox) makes that slow and
prone to lock errors. Override it anywhere, e.g. `LEMUR_BUILD_ROOT=~/lemur-build`.

On Windows, adding the build root to Defender's exclusions speeds builds up
noticeably.

---

## Releases

`.github/workflows/build.yml` is run manually (Actions → **Build installers** →
Run workflow). Each platform builds on its own native runner and publishes the
installer plus the auto-update metadata (`latest.yml`, `latest-mac.yml`) to a
GitHub Release named after the version in `package.json`. Installed copies of
Lemur check those releases and update themselves (Windows; macOS once builds
are signed — see below).

To ship: bump `version` in `package.json`, push, run the workflow.

---

## How it's wired

- **`lemur-server.spec`** — PyInstaller one-folder build. Bundles `templates/`,
  `static/`, `locale/` and `package.json`; excludes the dropped AI/OCR deps.
- **`app.py`** — resolves `templates/`/`static/` via `sys._MEIPASS` when frozen,
  else the source folder.
- **`electron/main.js`** — spawns the frozen `lemur-server` when packaged, or
  `python app.py` in development; pins the user-data folder and passes it to the
  backend as `LEMUR_DATA_DIR`.
- **`electron-builder.config.js`** — minimal asar (Electron wrapper only); the
  frozen backend ships via `extraResources`.
- **`build/icon.ico`, `build/icon.png`** — app icons (generated from `static/logo.png`).

## Why the build config looks the way it does

- **`win.signAndEditExecutable: false`** — code signing and rcedit's
  icon/version editing both pull electron-builder's *legacy* `winCodeSign`
  archive, which contains macOS dylib **symlinks**. Extracting symlinks on
  Windows needs admin or Developer Mode, so on a normal account the build fails
  with *"Cannot create symbolic link: A required privilege is not held by the
  client."* Skipping both avoids the archive. Trade-off: `Lemur.exe` shows the
  default Electron icon in Explorer; the installer, shortcuts and the running
  window all use `build/icon.ico`.
- **macOS builds are ad-hoc signed** (`identity: '-'`) so Apple Silicon doesn't
  report the app as "damaged"; users still see the right-click → Open prompt.

## Not yet done

- **Code signing.** Unsigned Windows installers trigger SmartScreen; unsigned
  macOS builds can't apply auto-updates and need right-click → Open on first
  launch. Needs a Windows code-signing certificate and an Apple Developer
  Program membership (Developer ID + notarization).
