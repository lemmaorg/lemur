# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Lemur Flask backend.

Produces a one-folder build (dist/lemur-server/lemur-server.exe) that the
Electron wrapper spawns in production instead of `python app.py`.

Bundled data:
  templates/      Jinja templates (index.html)
  static/         logos and other static assets
  package.json    read by /api/about for the version string

Build (from a clean venv with only requirements.txt + pyinstaller):
  pyinstaller lemur-server.spec --noconfirm \
      --distpath <out>/dist --workpath <out>/build
"""
from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    ['app.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'templates'),    'templates'),
        (str(ROOT / 'static'),       'static'),
        (str(ROOT / 'locale'),       'locale'),
        (str(ROOT / 'package.json'), '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Features dropped for v1 — keep them out of the bundle even if a stray
    # copy is installed in the build environment.
    excludes=['anthropic', 'pytesseract', 'PIL', 'tkinter', 'numpy', 'pandas'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='lemur-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # no console window when spawned by Electron
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # No icon here — this exe runs hidden as an Electron subprocess and is
    # never shown to the user. The window/taskbar icon is set by Electron.
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='lemur-server',
)
