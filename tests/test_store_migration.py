"""The one-time data-folder migration (earlier "Lemma" folder -> "Lemur")."""
from tests import sandbox  # noqa: F401  (must be first: isolates user folders)

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import store


class MigrateLegacyData(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.old = base / 'Lemma'
        self.new = base / 'Lemur'
        self.old.mkdir()
        self.new.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def seed_old(self):
        (self.old / 'library.json').write_text('[{"id": "b1"}]', encoding='utf-8')
        (self.old / 'settings.json').write_text('{"sr_theme": "warm"}', encoding='utf-8')
        (self.old / 'books' / 'b1').mkdir(parents=True)
        (self.old / 'books' / 'b1' / 'book.pdf').write_bytes(b'%PDF-1.4 test')
        (self.old / 'wallpaper.png').write_bytes(b'png')
        # Chromium profile folders share this directory on Windows — never copied.
        (self.old / 'Cache').mkdir()
        (self.old / 'Cache' / 'data_0').write_bytes(b'x' * 10)
        (self.old / 'Partitions' / 'lemma').mkdir(parents=True)

    def test_copies_the_store_data(self):
        self.seed_old()
        self.assertEqual(store.migrate_legacy_data(self.new, [self.old]), self.old)
        self.assertEqual((self.new / 'library.json').read_text(encoding='utf-8'), '[{"id": "b1"}]')
        self.assertTrue((self.new / 'books' / 'b1' / 'book.pdf').is_file())
        self.assertTrue((self.new / 'settings.json').is_file())
        self.assertTrue((self.new / 'wallpaper.png').is_file())

    def test_does_not_copy_chromium_folders(self):
        self.seed_old()
        store.migrate_legacy_data(self.new, [self.old])
        self.assertFalse((self.new / 'Cache').exists())
        self.assertFalse((self.new / 'Partitions').exists())

    def test_leaves_the_old_folder_intact(self):
        self.seed_old()
        store.migrate_legacy_data(self.new, [self.old])
        self.assertTrue((self.old / 'library.json').is_file())
        self.assertTrue((self.old / 'books' / 'b1' / 'book.pdf').is_file())

    def test_noop_when_the_new_folder_already_has_a_library(self):
        self.seed_old()
        (self.new / 'library.json').write_text('[]', encoding='utf-8')
        self.assertIsNone(store.migrate_legacy_data(self.new, [self.old]))
        self.assertEqual((self.new / 'library.json').read_text(encoding='utf-8'), '[]')
        self.assertFalse((self.new / 'books').exists())

    def test_ignores_folders_without_a_library(self):
        (self.old / 'settings.json').write_text('{}', encoding='utf-8')
        self.assertIsNone(store.migrate_legacy_data(self.new, [self.old]))
        self.assertFalse((self.new / 'settings.json').exists())

    def test_first_legacy_folder_with_data_wins(self):
        empty = Path(self._tmp.name) / 'SpeedReader'
        empty.mkdir()
        self.seed_old()
        self.assertEqual(store.migrate_legacy_data(self.new, [empty, self.old]), self.old)

    def test_an_interrupted_copy_is_retried_next_time(self):
        # library.json is copied last, so a failure part-way leaves the new
        # folder "not yet migrated" and the next launch simply tries again.
        self.seed_old()
        real_copy2 = store.shutil.copy2

        def failing_copy2(src, dst, *args, **kwargs):
            if str(src).endswith('settings.json'):
                raise OSError('disk full')
            return real_copy2(src, dst, *args, **kwargs)

        with mock.patch.object(store.shutil, 'copy2', failing_copy2):
            self.assertIsNone(store.migrate_legacy_data(self.new, [self.old]))
        self.assertFalse((self.new / 'library.json').exists())

        self.assertEqual(store.migrate_legacy_data(self.new, [self.old]), self.old)
        self.assertTrue((self.new / 'library.json').exists())


class MigrationIsAtomicForCallers(unittest.TestCase):
    def test_requests_during_migration_wait_for_the_copy(self):
        # A second caller arriving while the first-launch copy is still running
        # must not see the data folder before library.json has been copied.
        import threading
        import time
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {'LEMUR_DATA_DIR': d}), \
                mock.patch.object(store, '_migration_checked', False):

            def slow_migration(new_dir, legacy_dirs=None):
                time.sleep(0.3)
                (new_dir / 'library.json').write_text('[]', encoding='utf-8')

            seen = {}
            with mock.patch.object(store, 'migrate_legacy_data', slow_migration):
                first = threading.Thread(target=store._data_dir)
                first.start()
                time.sleep(0.05)                       # let the copy begin
                seen['library'] = (store._data_dir() / 'library.json').exists()
                first.join()
            self.assertTrue(seen['library'])


class ResolveDataDir(unittest.TestCase):
    def test_lemur_data_dir_env_wins(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'LEMUR_DATA_DIR': d}):
            self.assertEqual(store._resolve_data_dir(), Path(d))

    def test_default_folder_is_named_lemur(self):
        env = {k: v for k, v in os.environ.items() if k != 'LEMUR_DATA_DIR'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(store._resolve_data_dir().name, 'Lemur')

    def test_macos_uses_application_support(self):
        # Regression: earlier builds put data in a visible ~/Lemma folder on
        # macOS because they only consulted the Windows APPDATA variable.
        with mock.patch.object(store.sys, 'platform', 'darwin'):
            self.assertEqual(store._platform_data_root(),
                             Path.home() / 'Library' / 'Application Support')

    def test_legacy_search_covers_the_old_macos_home_folder(self):
        self.assertIn(Path.home() / 'Lemma', store._legacy_data_dirs())


if __name__ == '__main__':
    unittest.main()
