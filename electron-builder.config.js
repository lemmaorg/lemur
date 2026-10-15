/**
 * electron-builder configuration for Lemur (Windows + macOS).
 *
 * Architecture: the Electron wrapper (electron/ + package.json) goes into the
 * asar; the Python/Flask app is frozen separately by PyInstaller and shipped
 * unpacked under resources/lemur-server/ via extraResources. End users need
 * neither Python nor Node.
 *
 * Build output / backend location:
 *   - Local Windows dev keeps heavy I/O OUTSIDE OneDrive (C:/Lemur-build) to
 *     avoid sync locks. CI overrides these via env vars.
 *   - LEMUR_BUILD_ROOT   — base dir for output/    (default C:/Lemur-build)
 *   - LEMUR_BACKEND_DIST — frozen backend folder   (default <root>/dist/lemur-server)
 *   - LEMUR_OUTPUT_DIR   — installer output folder  (default <root>/installer)
 *
 * On CI we set LEMUR_BACKEND_DIST and LEMUR_OUTPUT_DIR to repo-relative dirs.
 */
const path = require('path');

const isWin = process.platform === 'win32';
const isMac = process.platform === 'darwin';

// Windows v1 ships UNSIGNED. Disable auto-discovery of any code-signing cert
// in the machine's store — otherwise electron-builder tries to sign with
// signtool, which pulls the legacy winCodeSign archive (whose macOS dylib
// symlinks fail to extract on Windows without admin). Harmless on macOS.
if (isWin) process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false';

const BUILD_ROOT   = process.env.LEMUR_BUILD_ROOT   || 'C:/Lemur-build';
const BACKEND_DIST = process.env.LEMUR_BACKEND_DIST || path.join(BUILD_ROOT, 'dist', 'lemur-server');
const OUTPUT_DIR   = process.env.LEMUR_OUTPUT_DIR   || path.join(BUILD_ROOT, 'installer');

// Auto-update channel: the GitHub Releases of Lemur's own public repository.
// CI publishes installers + update metadata (latest.yml / latest-mac.yml)
// there, and installed copies poll it. Owner/repo are overridable so a fork
// can ship from its own repository without a code change.
const PUBLISH = {
  provider: 'github',
  owner: process.env.LEMUR_RELEASES_OWNER || 'lemmaorg',
  repo:  process.env.LEMUR_RELEASES_REPO  || 'lemur',
  // Publish a LIVE release (not a draft) so testers receive the update as soon
  // as CI finishes. The workflow is manual (workflow_dispatch), so triggering a
  // build is already a deliberate "ship it". Switch to 'draft' if you'd rather
  // review the assets before they go live.
  releaseType: 'release',
};

module.exports = {
  // Lemur, made by Lemma. The appId is the app's stable identity (installer
  // GUID on Windows, bundle id on macOS) — don't change it again once public.
  appId: 'com.lemma.lemur',
  productName: 'Lemur',
  copyright: `Copyright © ${new Date().getFullYear()} Lemma`,

  // Where electron-updater looks for new versions (and where `--publish`
  // uploads on CI). See PUBLISH above.
  publish: PUBLISH,

  directories: {
    output: OUTPUT_DIR,
    buildResources: 'build',
  },

  // Main-process code + runtime node_modules. electron-builder auto-includes
  // production dependencies declared in package.json (currently the Discord
  // Rich Presence library and its deps), so they end up inside the asar.
  files: [
    'electron/**/*',
    'package.json',
  ],

  // The frozen backend ships unpacked under resources/. The whole folder's
  // contents land at resources/lemur-server/ on every platform; main.js
  // resolves the per-OS executable name inside it.
  // The Windows window/taskbar icon is read at runtime from resources/icon.ico
  // (macOS gets its icon from the generated .icns inside the .app bundle).
  extraResources: [
    { from: BACKEND_DIST, to: 'lemur-server' },
    ...(isWin ? [{ from: 'build/icon.ico', to: 'icon.ico' }] : []),
    // Logo for the native startup screen (shown before the backend can serve it).
    { from: 'static/lemurtext.png', to: 'lemurtext.png' },
  ],

  // ── Windows ──────────────────────────────────────────────────────────────
  win: {
    icon: 'build/icon.ico',
    target: [{ target: 'nsis', arch: ['x64'] }],
    artifactName: '${productName}-Setup-${version}.${ext}',
    // Unsigned local builds skip signtool + rcedit (both pull the legacy
    // winCodeSign archive whose macOS symlinks can't extract on Windows
    // without admin). On CI, signtool isn't auto-triggered either since we
    // ship unsigned — keeping this false is safe everywhere for v1.
    signAndEditExecutable: false,
  },
  nsis: {
    oneClick: false,
    perMachine: false,                      // per-user install — no admin prompt
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: 'Lemur',
    uninstallDisplayName: 'Lemur ${version}',
    // Auto-update downloads the full NSIS installer (no differential package).
    // electron-builder still emits latest.yml + the .exe.blockmap that
    // electron-updater needs to detect and verify the new version.
    differentialPackage: false,
  },

  // ── macOS ────────────────────────────────────────────────────────────────
  mac: {
    icon: 'build/icon.png',                 // electron-builder generates .icns
    category: 'public.app-category.education',
    // Build for whatever arch the CI matrix passes (arm64 or x64). The
    // workflow runs one job per arch so each artifact is single-architecture.
    // The .dmg is the first-time manual installer; the .zip is what
    // electron-updater downloads to APPLY a macOS auto-update (Squirrel.Mac
    // updates from a zip, not a dmg). Shipping the zip now means Mac
    // auto-update lights up as soon as the build is signed + notarized —
    // no further config change needed.
    target: [
      { target: 'dmg', arch: [process.env.LEMUR_MAC_ARCH || 'arm64'] },
      { target: 'zip', arch: [process.env.LEMUR_MAC_ARCH || 'arm64'] },
    ],
    artifactName: '${productName}-${version}-${arch}.${ext}',
    // v1 is NOT notarized (no paid Apple account), but we AD-HOC sign so the
    // app has a valid (if anonymous) signature. Without any signature,
    // Apple Silicon reports the app as "damaged" and blocks it outright;
    // ad-hoc signing downgrades that to the milder right-click → Open flow.
    // identity '-' tells electron-builder/codesign to ad-hoc sign locally.
    identity: '-',
    hardenedRuntime: false,                 // no notarization, so keep it off
    gatekeeperAssess: false,                // don't run Gatekeeper assess at build
    entitlements: 'build/entitlements.mac.plist',
    entitlementsInherit: 'build/entitlements.mac.plist',
  },
  dmg: {
    // Standard drag-to-Applications layout.
    contents: [
      { x: 130, y: 220, type: 'file' },
      { x: 410, y: 220, type: 'link', path: '/Applications' },
    ],
  },
};
