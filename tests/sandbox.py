"""
Import this FIRST in every test module.

It points every per-user folder (home, APPDATA, XDG data, the Lemur data dir)
at a throwaway directory before the app is imported, so a test run can never
read, write or migrate a real user's Lemur data.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_HOME = tempfile.mkdtemp(prefix='lemur-test-home-')
for _var in ('HOME', 'USERPROFILE'):
    os.environ[_var] = _HOME
os.environ['APPDATA'] = os.path.join(_HOME, 'AppData', 'Roaming')
os.environ['XDG_DATA_HOME'] = os.path.join(_HOME, '.local', 'share')
os.environ['LEMUR_DATA_DIR'] = os.path.join(_HOME, 'LemurData')

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
