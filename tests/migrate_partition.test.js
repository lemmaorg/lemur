// Storage-partition migration ("persist:lemma" -> "persist:lemur").
// Run with: node --test tests/
const test   = require('node:test');
const assert = require('node:assert');
const fs     = require('fs');
const os     = require('os');
const path   = require('path');
const { migratePartition } = require('../electron/migrate');

function sandbox() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lemur-partition-'));
  const appData  = path.join(root, 'AppData');
  const userData = path.join(appData, 'Lemur');
  fs.mkdirSync(userData, { recursive: true });
  return { root, appData, userData };
}

function seedPartition(dir) {
  fs.mkdirSync(path.join(dir, 'Local Storage', 'leveldb'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'Local Storage', 'leveldb', '000003.log'), 'prefs');
  fs.mkdirSync(path.join(dir, 'IndexedDB'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'IndexedDB', 'handles'), 'file-handles');
  fs.mkdirSync(path.join(dir, 'Cache'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'Cache', 'data_0'), 'cache');
}

test('copies the old Lemma profile partition into the Lemur one', () => {
  const { appData, userData } = sandbox();
  const old = path.join(appData, 'Lemma', 'Partitions', 'lemma');
  seedPartition(old);
  assert.strictEqual(migratePartition({ userData, appData }), old);
  const target = path.join(userData, 'Partitions', 'lemur');
  assert.ok(fs.existsSync(path.join(target, 'Local Storage', 'leveldb', '000003.log')));
  assert.ok(fs.existsSync(path.join(target, 'IndexedDB', 'handles')));
});

test('skips regenerable caches', () => {
  const { appData, userData } = sandbox();
  seedPartition(path.join(appData, 'Lemma', 'Partitions', 'lemma'));
  migratePartition({ userData, appData });
  assert.ok(!fs.existsSync(path.join(userData, 'Partitions', 'lemur', 'Cache')));
});

test('prefers a lemma partition already inside the Lemur profile', () => {
  const { appData, userData } = sandbox();
  const inProfile = path.join(userData, 'Partitions', 'lemma');
  seedPartition(inProfile);
  seedPartition(path.join(appData, 'Lemma', 'Partitions', 'lemma'));
  assert.strictEqual(migratePartition({ userData, appData }), inProfile);
});

test('leaves the old partition in place as a backup', () => {
  const { appData, userData } = sandbox();
  const old = path.join(appData, 'Lemma', 'Partitions', 'lemma');
  seedPartition(old);
  migratePartition({ userData, appData });
  assert.ok(fs.existsSync(path.join(old, 'Local Storage', 'leveldb', '000003.log')));
});

test('is a no-op once the Lemur partition exists', () => {
  const { appData, userData } = sandbox();
  seedPartition(path.join(appData, 'Lemma', 'Partitions', 'lemma'));
  fs.mkdirSync(path.join(userData, 'Partitions', 'lemur'), { recursive: true });
  assert.strictEqual(migratePartition({ userData, appData }), null);
  assert.ok(!fs.existsSync(path.join(userData, 'Partitions', 'lemur', 'IndexedDB')));
});

test('does nothing (and creates nothing) for a fresh install', () => {
  const { appData, userData } = sandbox();
  assert.strictEqual(migratePartition({ userData, appData }), null);
  assert.ok(!fs.existsSync(path.join(userData, 'Partitions')));
});

test('a failed copy never leaves a half-written live partition', () => {
  const { appData, userData } = sandbox();
  seedPartition(path.join(appData, 'Lemma', 'Partitions', 'lemma'));
  const realRename = fs.renameSync;
  fs.renameSync = () => { throw new Error('simulated failure'); };
  try {
    assert.strictEqual(migratePartition({ userData, appData }), null);
  } finally {
    fs.renameSync = realRename;
  }
  assert.ok(!fs.existsSync(path.join(userData, 'Partitions', 'lemur')));
  assert.ok(!fs.existsSync(path.join(userData, 'Partitions', 'lemur.migrating')));
});
