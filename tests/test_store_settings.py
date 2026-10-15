"""Settings persistence under concurrent writes (the page fires many at once)."""
from tests import sandbox  # noqa: F401  (must be first: isolates user folders)

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import store


class ConcurrentSettings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {'LEMUR_DATA_DIR': self._tmp.name})
        self._env.start()
        self.s = store.DataStore()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_parallel_writes_keep_every_key(self):
        errors = []

        def write(i):
            try:
                self.s.set_setting(f'k{i}', i)
            except Exception as e:          # pragma: no cover - asserted below
                errors.append(e)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(60)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        saved = self.s.get_settings()
        self.assertEqual({k: saved[k] for k in saved if k.startswith('k')},
                         {f'k{i}': i for i in range(60)})

    def test_no_temp_files_left_behind(self):
        for i in range(20):
            self.s.set_setting(f'k{i}', i)
        self.assertEqual(list(Path(self._tmp.name).glob('*.tmp')), [])

    def test_bulk_merge_never_drops_unmentioned_keys(self):
        self.s.set_setting('sr_theme', 'warm')
        self.s.merge_settings({'sr_lang': 'es'})
        self.assertEqual(self.s.get_settings(), {'sr_theme': 'warm', 'sr_lang': 'es'})

    def test_none_removes_a_key(self):
        self.s.merge_settings({'a': 1, 'b': 2})
        self.s.set_setting('a', None)
        self.assertEqual(json.loads((Path(self._tmp.name) / 'settings.json').read_text()), {'b': 2})


if __name__ == '__main__':
    unittest.main()
