/**
 * One-time migration of the Chromium storage partition to Lemur's name.
 *
 * Earlier releases used the partition "persist:lemma", stored under the
 * "Lemma" (or, for some packaged builds, already "Lemur") profile folder. The
 * partition holds localStorage (UI preferences, per-book caches) and IndexedDB
 * (remembered file handles), so it is carried over into "persist:lemur".
 *
 * Safety: the partition is COPIED (never moved) into a temporary folder and
 * only then renamed into place, so an interrupted copy never becomes the live
 * partition, and the old copy stays behind as a backup.
 *
 * Pure Node — no Electron imports — so it can be unit-tested with temp dirs.
 */
const fs   = require('fs');
const path = require('path');

// Regenerable Chromium caches: never worth copying.
const SKIP = new Set([
  'Cache', 'Code Cache', 'GPUCache', 'DawnCache', 'DawnGraphiteCache',
  'DawnWebGPUCache', 'Service Worker', 'blob_storage',
]);

/** Where an earlier release may have left its partition, most likely first. */
function legacyPartitionDirs(userData, appData) {
  return [...new Set([
    path.join(userData, 'Partitions', 'lemma'),
    path.join(appData, 'Lemma', 'Partitions', 'lemma'),
    path.join(appData, 'lemma', 'Partitions', 'lemma'),
  ])];
}

/**
 * Copy the first legacy partition found into <userData>/Partitions/<to>.
 * No-op when the target already exists. Returns the source used, or null.
 */
function migratePartition({ userData, appData, to = 'lemur', sources, log = () => {} }) {
  const target = path.join(userData, 'Partitions', to);
  if (fs.existsSync(target)) return null;
  for (const src of (sources || legacyPartitionDirs(userData, appData))) {
    let isDir = false;
    try { isDir = fs.statSync(src).isDirectory(); } catch (_) {}
    if (!isDir) continue;
    const tmp = target + '.migrating';
    try {
      fs.rmSync(tmp, { recursive: true, force: true });
      fs.cpSync(src, tmp, { recursive: true, filter: p => !SKIP.has(path.basename(p)) });
      fs.renameSync(tmp, target);
      log(`[migrate] copied storage partition ${src} -> ${target}`);
      return src;
    } catch (e) {
      log(`[migrate] partition copy from ${src} failed (starting fresh): ${e.message}`);
      try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
      return null;
    }
  }
  return null;
}

module.exports = { migratePartition, legacyPartitionDirs };
