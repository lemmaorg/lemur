/**
 * Freezes the Flask backend with PyInstaller for a local installer build.
 *
 * Portable on purpose — no hardcoded machine paths and no hardcoded venv:
 *   - `pyinstaller` is resolved from PATH (activate your venv, or have it
 *     installed), so this runs unchanged on Windows and macOS.
 *   - Output location mirrors electron-builder.config.js exactly via the same
 *     LEMUR_BUILD_ROOT env var (default C:/Lemur-build on the maintainer's
 *     Windows box to keep heavy I/O out of OneDrive; override anywhere else).
 *     PyInstaller writes <root>/dist/lemur-server, which is precisely where the
 *     installer config's BACKEND_DIST default looks.
 *
 * CI doesn't use this script — it calls pyinstaller directly with repo-relative
 * paths (see .github/workflows/build.yml).
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';

const BUILD_ROOT = process.env.LEMUR_BUILD_ROOT || 'C:/Lemur-build';
const distPath = path.join(BUILD_ROOT, 'dist');     // → <root>/dist/lemur-server
const workPath = path.join(BUILD_ROOT, 'build');

const args = [
  'lemur-server.spec',
  '--noconfirm',
  '--distpath', distPath,
  '--workpath', workPath,
];

console.log(`[build-backend] pyinstaller → ${distPath} (root: ${BUILD_ROOT})`);

// shell:true lets the OS resolve `pyinstaller` from PATH on both platforms.
const res = spawnSync('pyinstaller', args, { stdio: 'inherit', shell: true });

if (res.error) {
  console.error('[build-backend] failed to launch pyinstaller — is it on PATH ' +
                '(venv activated)?\n', res.error.message);
  process.exit(1);
}
process.exit(res.status ?? 1);
