"""
Lemur persistent data store.

All user data lives in one per-user folder named "Lemur":
  Windows  %APPDATA%\\Lemur
  macOS    ~/Library/Application Support/Lemur
  Linux    $XDG_DATA_HOME/Lemur  (default ~/.local/share/Lemur)
Electron passes the exact folder via LEMUR_DATA_DIR; the defaults above apply
when the backend runs on its own (`python app.py`).
"""
import os, sys, json, zipfile, io, time, re, shutil, threading, functools, itertools
from pathlib import Path

# One re-entrant lock serialises every store operation. Flask serves requests
# on several threads, and the page fires many settings writes at once during
# startup; unserialised read-modify-write cycles overwrote each other (a lost
# 'sr_theme' write brought an old theme back on the next launch).
_LOCK = threading.RLock()
_tmp_counter = itertools.count()


# ── Path helpers ───────────────────────────────────────────────────────────

APP_DIRNAME = 'Lemur'

# Folder names used by earlier releases, newest first. Their data is copied
# into the Lemur folder once (see migrate_legacy_data).
_LEGACY_DIRNAMES = ('Lemma', 'SpeedReader')

# Everything the store owns inside its folder. Migration copies ONLY these, so
# the Electron/Chromium cache folders that share the directory on Windows are
# never duplicated. library.json is handled separately — it is copied last and
# its presence marks a completed migration.
_DATA_ITEMS = ('books', 'collage-assets', 'backups', 'settings.json',
               'collections.json', 'collages.json', 'watched_dirs.json')


def _platform_data_root() -> Path:
    """The OS-conventional parent folder for per-user application data."""
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')


def _resolve_data_dir() -> Path:
    env = os.environ.get('LEMUR_DATA_DIR')
    return Path(env) if env else _platform_data_root() / APP_DIRNAME


def _legacy_data_dirs() -> list:
    """Where earlier releases kept data. They used %APPDATA%\\<name> on Windows
    and — because APPDATA is unset elsewhere — ~/<name> on macOS and Linux."""
    roots = [_platform_data_root(), Path.home()]
    if os.environ.get('APPDATA'):
        roots.insert(0, Path(os.environ['APPDATA']))
    out = []
    for name in _LEGACY_DIRNAMES:
        for root in roots:
            p = root / name
            if p not in out:
                out.append(p)
    return out


def migrate_legacy_data(new_dir: Path, legacy_dirs=None):
    """Copy user data from an earlier release's folder into new_dir, once.

    Runs only while new_dir has no library.json. Copies (never moves), so the
    old folder survives as a backup; library.json goes last, so an interrupted
    copy is simply retried on the next launch. Returns the source folder used,
    or None when nothing was migrated.
    """
    if (new_dir / 'library.json').exists():
        return None
    for old in (legacy_dirs if legacy_dirs is not None else _legacy_data_dirs()):
        try:
            if old.resolve() == new_dir.resolve():
                continue
        except OSError:
            continue
        if not (old / 'library.json').is_file():
            continue
        try:
            for name in _DATA_ITEMS:
                src = old / name
                if src.is_dir():
                    shutil.copytree(src, new_dir / name, dirs_exist_ok=True)
                elif src.is_file():
                    shutil.copy2(src, new_dir / name)
            for wallpaper in old.glob('wallpaper.*'):
                shutil.copy2(wallpaper, new_dir / wallpaper.name)
            shutil.copy2(old / 'library.json', new_dir / 'library.json')
        except OSError as e:
            print(f"[store] migration from {old} incomplete, will retry next launch: {e}")
            return None
        print(f"[store] migrated user data: {old} -> {new_dir}")
        return old
    return None


_migration_checked = False


def _data_dir() -> Path:
    global _migration_checked
    p = _resolve_data_dir()
    p.mkdir(parents=True, exist_ok=True)
    if not _migration_checked:
        # Under the store lock, and the flag is only set once the copy is done,
        # so a request arriving mid-migration waits instead of seeing an
        # empty, not-yet-copied library.
        with _LOCK:
            if not _migration_checked:
                migrate_legacy_data(p)
                _migration_checked = True
    return p


def _books_dir() -> Path:
    p = _data_dir() / 'books'
    p.mkdir(exist_ok=True)
    return p


def safe_id(name: str) -> str:
    """Convert a filename/title to a safe directory name used as book ID."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    # Strip leading/trailing underscores, spaces, and dots.
    # Trailing dots are illegal in Windows directory names — the OS silently
    # strips them on creation, causing every subsequent path lookup to fail.
    return s[:120].strip('_ .') or 'book'


def _book_dir(book_id: str) -> Path:
    p = _books_dir() / book_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── JSON helpers ───────────────────────────────────────────────────────────

def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _atomic_write(path: Path, data: bytes):
    """Crash-safe write: stream to a sibling temp file, flush+fsync it, then
    atomically rename over the target. A crash or full disk can leave the .tmp
    behind but never a half-written/corrupted real file. os.replace is atomic
    on the same filesystem (the temp is always in the target's own directory)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name per write: a shared "<name>.tmp" let two concurrent
    # writers clobber each other's temp file (WinError 32 / 5 on Windows).
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{next(_tmp_counter)}.tmp')
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Windows can briefly lock the target (antivirus, search indexer);
        # retry the atomic swap a few times before giving up.
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.025 * (attempt + 1))
    except Exception:
        # Clean up a stray temp so it can't be mistaken for real data later.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def _write_json(path: Path, data):
    _atomic_write(path, json.dumps(data, ensure_ascii=False).encode('utf-8'))


def _write_text(path: Path, text: str):
    _atomic_write(path, (text or '').encode('utf-8'))


def _write_bytes(path: Path, data: bytes):
    _atomic_write(path, data)


def _read_json_bytes(b: bytes, default=None):
    try:
        return json.loads(b.decode('utf-8'))
    except Exception:
        return default


# ── DataStore ──────────────────────────────────────────────────────────────

def _serialized(cls):
    """Run every method of the class under the store-wide lock (_LOCK).

    Several methods write lazily or do read-modify-write across calls, so
    locking a hand-picked subset would be easy to get wrong; serialising all of
    them is simple and costs nothing noticeable in a single-user local app.
    """
    for name, fn in list(vars(cls).items()):
        if name.startswith('__') or not callable(fn):
            continue

        def wrap(f):
            @functools.wraps(f)
            def locked(*args, **kwargs):
                with _LOCK:
                    return f(*args, **kwargs)
            return locked
        setattr(cls, name, wrap(fn))
    return cls


@_serialized
class DataStore:
    """All persistence operations for Lemur."""

    # ── Library index ──────────────────────────────────────────────────────

    def get_library(self) -> list:
        return _read_json(_data_dir() / 'library.json', [])

    def upsert_library(self, entry: dict):
        """Insert/update a library entry keyed by entry['id'].

        Merges with the existing entry so partial updates (e.g. just
        {id, currentIndex}) don't overwrite unrelated fields.
        """
        book_id = entry.get('id')
        if not book_id:
            return
        lib      = self.get_library()
        existing = next((e for e in lib if e.get('id') == book_id), {})
        merged   = {**existing, **entry}          # caller fields win
        lib      = [e for e in lib if e.get('id') != book_id]
        lib.insert(0, merged)
        if len(lib) > 500:
            lib = lib[:500]
        _write_json(_data_dir() / 'library.json', lib)

    def remove_from_library(self, book_id: str):
        lib = [e for e in self.get_library() if e.get('id') != book_id]
        _write_json(_data_dir() / 'library.json', lib)

    # Annotation/data files (everything except the book file + thumb).
    _DATA_FILES = ('notes.txt', 'bookmarks.json', 'drawings.json',
                   'notedraw.json', 'mindmap.json', 'illusbms.json', 'theme.json')

    def reset_book_data(self, book_id: str, vid: str | None = None):
        """Clear a book's annotations while keeping the book file + thumbnail.
        Resets the active version (or a specified one) — its files are emptied,
        but the version itself and other versions remain. A per-book 'reset'."""
        self.get_versions(book_id)            # ensure migrated/initialised
        vid = vid or self._active_vid(book_id)
        vdir = self._version_dir(book_id, vid)
        for fname in self._DATA_FILES:
            p = vdir / fname
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    def delete_book(self, book_id: str):
        """Remove from the library AND delete all stored data for the book
        (annotations, book file, thumbnail) — the whole book directory.
        Does NOT touch the user's original source file on disk."""
        import shutil
        self.remove_from_library(book_id)
        bd = _books_dir() / book_id
        try:
            if bd.exists():
                shutil.rmtree(bd, ignore_errors=True)
        except OSError:
            pass

    def has_pdf(self, book_id: str) -> bool:
        """True if a stored PDF exists.  Use has_book() to cover EPUB too."""
        return (_book_dir(book_id) / 'book.pdf').exists()

    def has_book(self, book_id: str) -> bool:
        """True if any stored book file (PDF or EPUB) exists."""
        bd = _book_dir(book_id)
        return (bd / 'book.pdf').exists() or (bd / 'book.epub').exists()

    # ── Thumbnails (generated lazily, cached on disk) ──────────────────────

    def get_thumb(self, book_id: str) -> bytes | None:
        p = _book_dir(book_id) / 'thumb.jpg'
        return p.read_bytes() if p.exists() else None

    def save_thumb(self, book_id: str, jpeg_bytes: bytes):
        _write_bytes(_book_dir(book_id) / 'thumb.jpg', jpeg_bytes)

    def has_thumb(self, book_id: str) -> bool:
        return (_book_dir(book_id) / 'thumb.jpg').exists()

    # ── Watched directories ────────────────────────────────────────────────

    def get_watched_dirs(self) -> list:
        return _read_json(_data_dir() / 'watched_dirs.json', [])

    def save_watched_dirs(self, dirs: list):
        _write_json(_data_dir() / 'watched_dirs.json', dirs)

    def add_watched_dir(self, path: str) -> list:
        dirs = self.get_watched_dirs()
        norm = os.path.normpath(path)
        if norm not in dirs:
            dirs.append(norm)
            self.save_watched_dirs(dirs)
        return dirs

    def remove_watched_dir(self, path: str) -> list:
        norm = os.path.normpath(path)
        dirs = [d for d in self.get_watched_dirs() if os.path.normpath(d) != norm]
        self.save_watched_dirs(dirs)
        return dirs

    def scan_watched_dirs(self) -> list:
        """Return a flat list of PDF and EPUB dicts found in all watched directories."""
        import glob
        results = []
        seen_ids = set()
        for d in self.get_watched_dirs():
            if not os.path.isdir(d):
                continue
            file_paths = []
            for pat in ('*.pdf', '*.epub', '*.epub.zip'):
                file_paths.extend(
                    glob.glob(os.path.join(d, '**', pat), recursive=True))
            for file_path in sorted(set(file_paths)):
                file_path = os.path.normpath(file_path)
                name      = os.path.basename(file_path)
                bid       = safe_id(name)
                if bid in seen_ids:
                    continue
                seen_ids.add(bid)
                try:
                    stat = os.stat(file_path)
                    results.append({
                        'id':       bid,
                        'name':     name,
                        'path':     file_path,
                        'dir':      d,
                        'mtime':    int(stat.st_mtime * 1000),
                        'size':     stat.st_size,
                        'hasThumb': self.has_thumb(bid),
                        'hasPdf':   self.has_book(bid),  # true for both PDF and EPUB
                    })
                except OSError:
                    continue
        return results

    # ── Book file storage (PDF and EPUB) ───────────────────────────────────

    def save_pdf(self, book_id: str, pdf_bytes: bytes):
        _write_bytes(_book_dir(book_id) / 'book.pdf', pdf_bytes)

    def get_pdf(self, book_id: str) -> bytes | None:
        p = _book_dir(book_id) / 'book.pdf'
        return p.read_bytes() if p.exists() else None

    def save_epub(self, book_id: str, epub_bytes: bytes):
        _write_bytes(_book_dir(book_id) / 'book.epub', epub_bytes)

    def get_epub(self, book_id: str) -> bytes | None:
        p = _book_dir(book_id) / 'book.epub'
        return p.read_bytes() if p.exists() else None

    def get_book(self, book_id: str) -> tuple:
        """Return (bytes, file_type) for the stored book file, or (None, None)."""
        bd = _book_dir(book_id)
        pdf = bd / 'book.pdf'
        if pdf.exists():
            return pdf.read_bytes(), 'pdf'
        epub = bd / 'book.epub'
        if epub.exists():
            return epub.read_bytes(), 'epub'
        return None, None

    # ── Per-book user data — VERSIONED ─────────────────────────────────────
    # Each book has one or more named annotation "versions". A version is a
    # subfolder under <book>/versions/<vid>/ holding the annotation files
    # (notes.txt, bookmarks.json, drawings.json, notedraw.json, mindmap.json,
    # illusbms.json, theme.json). <book>/versions/manifest.json records the
    # version list + which one is active:  {activeId, versions:[{id,name,created}]}
    #
    # Legacy books stored annotation files flat in the book dir; on first access
    # those are migrated into a version named "Original".
    _DATA_KEYS = ('notes', 'bookmarks', 'drawings', 'notedraw',
                  'mindmap', 'illusbms', 'theme')

    def _versions_dir(self, book_id: str) -> Path:
        p = _book_dir(book_id) / 'versions'
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _manifest_path(self, book_id: str) -> Path:
        return self._versions_dir(book_id) / 'manifest.json'

    def _new_vid(self) -> str:
        return str(int(time.time() * 1000))

    def _migrate_legacy(self, book_id: str):
        """If a book has flat annotation files but no versions yet, move them
        into an 'Original' version. Idempotent."""
        bd = _book_dir(book_id)
        manifest = self._manifest_path(book_id)
        if manifest.exists():
            return
        vid = self._new_vid()
        vdir = self._versions_dir(book_id) / vid
        vdir.mkdir(parents=True, exist_ok=True)
        moved = False
        for fname in ('notes.txt', 'bookmarks.json', 'drawings.json',
                      'notedraw.json', 'mindmap.json', 'illusbms.json', 'theme.json'):
            src = bd / fname
            if src.exists():
                try:
                    src.replace(vdir / fname)
                    moved = True
                except OSError:
                    pass
        # Name the user's own pre-existing set after them (not "Original"), so
        # it reads clearly next to imported sets like "Xia".
        _write_json(manifest, {
            'activeId': vid,
            'versions': [{'id': vid, 'name': self.get_user_name(),
                          'created': int(time.time() * 1000)}],
        })
        return moved

    def get_versions(self, book_id: str) -> dict:
        """Return {activeId, versions:[{id,name,created}]} (migrating if needed)."""
        self._migrate_legacy(book_id)
        m = _read_json(self._manifest_path(book_id), None)
        if not m or not m.get('versions'):
            # No data at all yet — create the user's own default version.
            vid = self._new_vid()
            m = {'activeId': vid,
                 'versions': [{'id': vid, 'name': self.get_user_name(),
                               'created': int(time.time() * 1000)}]}
            _write_json(self._manifest_path(book_id), m)
            (self._versions_dir(book_id) / vid).mkdir(parents=True, exist_ok=True)
        return m

    def _active_vid(self, book_id: str) -> str:
        return self.get_versions(book_id)['activeId']

    def _version_dir(self, book_id: str, vid: str) -> Path:
        p = self._versions_dir(book_id) / vid
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _unique_version_name(self, m: dict, name: str) -> str:
        """Return a name not already used by another version (appends ' (2)', …)."""
        base = (name or 'Untitled').strip() or 'Untitled'
        existing = {v['name'] for v in m['versions']}
        if base not in existing:
            return base
        n = 2
        while f'{base} ({n})' in existing:
            n += 1
        return f'{base} ({n})'

    def add_version(self, book_id: str, name: str, data: dict | None = None,
                    make_active: bool = True) -> str:
        """Create a new named version (optionally seeded with data). Returns vid.
        Names are de-duplicated so two versions never share a name."""
        m = self.get_versions(book_id)
        vid = self._new_vid()
        uniq = self._unique_version_name(m, name)
        m['versions'].append({'id': vid, 'name': uniq,
                              'created': int(time.time() * 1000)})
        if make_active:
            m['activeId'] = vid
        _write_json(self._manifest_path(book_id), m)
        self._version_dir(book_id, vid)
        if data:
            for key in self._DATA_KEYS:
                if key in data:
                    self.save_data(book_id, key, data[key], vid=vid)
        return vid

    def rename_version(self, book_id: str, vid: str, name: str) -> bool:
        m = self.get_versions(book_id)
        # De-dup against the OTHER versions (exclude the one being renamed).
        others = {'versions': [v for v in m['versions'] if v['id'] != vid]}
        uniq = self._unique_version_name(others, name)
        for v in m['versions']:
            if v['id'] == vid:
                v['name'] = uniq
                _write_json(self._manifest_path(book_id), m)
                return True
        return False

    def set_active_version(self, book_id: str, vid: str) -> bool:
        m = self.get_versions(book_id)
        if any(v['id'] == vid for v in m['versions']):
            m['activeId'] = vid
            _write_json(self._manifest_path(book_id), m)
            return True
        return False

    def delete_version(self, book_id: str, vid: str) -> bool:
        import shutil
        m = self.get_versions(book_id)
        if len(m['versions']) <= 1:
            return False   # never delete the last version
        m['versions'] = [v for v in m['versions'] if v['id'] != vid]
        if m['activeId'] == vid:
            m['activeId'] = m['versions'][0]['id']
        _write_json(self._manifest_path(book_id), m)
        try:
            shutil.rmtree(self._versions_dir(book_id) / vid, ignore_errors=True)
        except OSError:
            pass
        return True

    def save_data(self, book_id: str, key: str, data, vid: str | None = None):
        vid = vid or self._active_vid(book_id)
        vd  = self._version_dir(book_id, vid)
        if key == 'notes':
            _write_text(vd / 'notes.txt', data if isinstance(data, str) else '')
        else:
            _write_json(vd / f'{key}.json', data)

    def get_data(self, book_id: str, key: str, default=None, vid: str | None = None):
        vid = vid or self._active_vid(book_id)
        vd  = self._version_dir(book_id, vid)
        if key == 'notes':
            p = vd / 'notes.txt'
            return p.read_text(encoding='utf-8') if p.exists() else (default or '')
        else:
            return _read_json(vd / f'{key}.json', default)

    def get_all_data(self, book_id: str, vid: str | None = None) -> dict:
        return {
            'notes':     self.get_data(book_id, 'notes',     '',  vid),
            'bookmarks': self.get_data(book_id, 'bookmarks', [],  vid),
            'drawings':  self.get_data(book_id, 'drawings',  {},  vid),
            'notedraw':  self.get_data(book_id, 'notedraw',  [],  vid),
            'mindmap':   self.get_data(book_id, 'mindmap',   {},  vid),
            'illusbms':  self.get_data(book_id, 'illusbms',  [],  vid),
            'theme':     self.get_data(book_id, 'theme',     {},  vid),
        }

    # ── .srbook export / import ────────────────────────────────────────────

    # Map an annotation category key → its archive filename.
    _KEY_FILE = {
        'notes': 'notes.txt', 'bookmarks': 'bookmarks.json',
        'drawings': 'drawings.json', 'notedraw': 'notedraw.json',
        'mindmap': 'mindmap.json', 'illusbms': 'illusbms.json',
        'theme': 'theme.json',
    }

    def export_srbook(self, book_id: str, doc_bytes: bytes | None = None,
                      include_pdf: bool = True, extra_meta: dict | None = None,
                      file_type: str = 'pdf', vid: str | None = None,
                      categories: list | None = None) -> bytes:
        """Return ZIP bytes for a .srbook archive.

        Args:
            doc_bytes:   Caller-supplied book bytes; falls back to stored copy.
            include_pdf: False → annotations-only archive.
            extra_meta:  Extra fields merged into meta.json (e.g. currentIndex).
            file_type:   'pdf' or 'epub'.
            vid:         Which version to export (defaults to the active one).
            categories:  Optional list of annotation keys to include
                         (e.g. ['notes','bookmarks']); None → all categories.
        """
        vid = vid or self._active_vid(book_id)
        vdir = self._version_dir(book_id, vid)
        keys = categories if categories else list(self._DATA_KEYS)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            if include_pdf:
                book = doc_bytes
                if not book:
                    book, file_type = self.get_book(book_id)
                if book:
                    entry_name = 'book.epub' if file_type == 'epub' else 'book.pdf'
                    zf.writestr(entry_name, book)
            # Annotation files from the chosen version, filtered by category.
            for key in keys:
                fname = self._KEY_FILE.get(key)
                if not fname:
                    continue
                p = vdir / fname
                if p.exists():
                    zf.writestr(fname, p.read_bytes())
            # Metadata (records which version/categories this archive holds).
            lib   = self.get_library()
            entry = next((e for e in lib if e.get('id') == book_id), {'id': book_id})
            vname = next((v['name'] for v in self.get_versions(book_id)['versions']
                          if v['id'] == vid), 'Original')
            meta_out = {**entry, 'exported': int(time.time()),
                        'versionName': vname, 'categories': keys,
                        'exportedBy': self.get_user_name()}
            if extra_meta:
                meta_out.update(extra_meta)
            zf.writestr('meta.json', json.dumps(meta_out, ensure_ascii=False))
        return buf.getvalue()

    def import_srbook(self, zip_bytes: bytes, version_name: str | None = None) -> tuple:
        """
        Import a .srbook archive. The annotations always land in a NEW named
        version (never overwriting existing ones), which becomes active.
        Returns (book_id, meta, book_bytes_or_None, file_type_or_None, vid).
        """
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = set(zf.namelist())
            meta    = _read_json_bytes(zf.read('meta.json')) if 'meta.json' in names else {}
            # ALWAYS sanitize the id. A .srbook from another machine may carry a
            # raw filename (spaces, parentheses) as its id; using it verbatim as
            # a folder name diverges from safe_id() used everywhere else, so the
            # PDF and annotations end up in different folders ("Failed to open
            # stream", missing annotations, duplicate versions).
            book_id = safe_id(meta.get('id') or meta.get('name', 'imported'))

            if 'book.pdf' in names:
                book_bytes, file_type = zf.read('book.pdf'), 'pdf'
            elif 'book.epub' in names:
                book_bytes, file_type = zf.read('book.epub'), 'epub'
            else:
                book_bytes, file_type = None, None

            bd = _book_dir(book_id)
            if book_bytes:
                _write_bytes(bd / ('book.epub' if file_type == 'epub' else 'book.pdf'), book_bytes)

            # Collect the archive's annotations into a dict for the new version.
            data = {}
            if 'notes.txt' in names:
                data['notes'] = zf.read('notes.txt').decode('utf-8', 'replace')
            for key, fname in self._KEY_FILE.items():
                if key == 'notes':
                    continue
                if fname in names:
                    data[key] = _read_json_bytes(zf.read(fname))

            # Ensure the book's version store exists (migrates legacy if present),
            # then add the imported data as a new active version. Default the
            # version name to the SENDER (exportedBy) so shared files don't all
            # collapse to "Original"/"Imported". Caller-supplied version_name
            # (from the rename prompt) wins.
            self.get_versions(book_id)
            default_name = (version_name
                            or meta.get('exportedBy')
                            or meta.get('versionName')
                            or 'Imported')
            vid = self.add_version(book_id, default_name, data=data, make_active=True)

            meta['id'] = book_id
            meta['lastOpened'] = int(time.time() * 1000)
            if file_type:
                meta.setdefault('fileType', file_type)
            if meta.get('name'):
                self.upsert_library(meta)

            return book_id, meta, book_bytes, file_type, vid


    # ── App settings (port-independent; replaces localStorage) ─────────────────
    # A single key/value blob in the data dir, so UI preferences (theme, icon
    # set, highlight color, layout, …) survive regardless of which port the
    # backend happens to bind. localStorage is origin-scoped (includes port),
    # so it can't be the source of truth when the port varies between launches.

    def get_settings(self) -> dict:
        return _read_json(_data_dir() / 'settings.json', {}) or {}

    def save_settings(self, data: dict):
        if isinstance(data, dict):
            _write_json(_data_dir() / 'settings.json', data)

    def get_user_name(self) -> str:
        """The user's display name — used to name their own annotation set and
        stamped into exports so recipients see who sent them. Stored in settings
        (key 'sr_user_name'); defaults to the OS login name on first run."""
        name = (self.get_settings().get('sr_user_name') or '').strip()
        if name:
            return name
        try:
            import getpass
            return (getpass.getuser() or 'Me').strip() or 'Me'
        except Exception:
            return 'Me'

    def set_setting(self, key: str, value):
        self.merge_settings({key: value})

    def merge_settings(self, updates: dict):
        """Apply several keys at once on top of the saved settings (a None value
        removes the key). Never drops keys that aren't mentioned."""
        if not isinstance(updates, dict):
            return
        s = self.get_settings()
        for key, value in updates.items():
            if value is None:
                s.pop(key, None)
            else:
                s[key] = value
        self.save_settings(s)

    # ── Collections ───────────────────────────────────────────────────────────

    def get_collections(self) -> list:
        """Return list of collection dicts: [{id, name, bookIds:[...]}, ...]"""
        return _read_json(_data_dir() / 'collections.json', [])

    def save_collections(self, cols: list):
        _write_json(_data_dir() / 'collections.json', cols)

    def add_collection(self, name: str) -> dict:
        cols = self.get_collections()
        col  = {'id': str(int(time.time() * 1000)), 'name': name, 'bookIds': []}
        cols.append(col)
        self.save_collections(cols)
        return col

    def update_collection(self, col_id: str, name: str) -> bool:
        cols = self.get_collections()
        for c in cols:
            if c.get('id') == col_id:
                c['name'] = name
                self.save_collections(cols)
                return True
        return False

    def delete_collection(self, col_id: str):
        cols = [c for c in self.get_collections() if c.get('id') != col_id]
        self.save_collections(cols)

    def set_collection_books(self, col_id: str, book_ids: list) -> bool:
        """Replace the bookIds list for a collection."""
        cols = self.get_collections()
        for c in cols:
            if c.get('id') == col_id:
                c['bookIds'] = list(book_ids)
                self.save_collections(cols)
                return True
        return False

    # ── Collage (cross-book scrapbook notebook) ────────────────────────────────
    # A single global document in the data dir, separate from per-book data:
    #   collages.json = { boards: [ {id, name, created, view, items, strokes} ],
    #                     tray: [ {id, type, ...payload} ] }
    # Snapshotted images (illustration cut-outs, dropped images) are stored as
    # real files under collage-assets/ so a clipping survives even if its source
    # book is deleted. Both ride along in the whole-library backup automatically.

    def _collage_assets_dir(self) -> Path:
        p = _data_dir() / 'collage-assets'
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_collages(self) -> dict:
        d = _read_json(_data_dir() / 'collages.json', None)
        if not isinstance(d, dict):
            d = {}
        d.setdefault('boards', [])
        d.setdefault('tray', [])
        return d

    def save_collages(self, data: dict):
        if not isinstance(data, dict):
            return
        out = {'boards': data.get('boards', []), 'tray': data.get('tray', [])}
        _write_json(_data_dir() / 'collages.json', out)

    def save_collage_asset(self, data_bytes: bytes, ext: str = 'png') -> str:
        """Store an image snapshot; returns the asset filename (its id+ext)."""
        ext = (ext or 'png').lstrip('.').lower()
        if ext not in ('png', 'jpg', 'jpeg', 'webp'):
            ext = 'png'
        aid = str(int(time.time() * 1000)) + '-' + os.urandom(3).hex()
        name = f'{aid}.{ext}'
        _write_bytes(self._collage_assets_dir() / name, data_bytes)
        return name

    def get_collage_asset(self, name: str) -> bytes | None:
        """Read one asset by name (matched to the assets folder only)."""
        p = self._collage_assets_dir() / Path(name).name
        return p.read_bytes() if p.exists() else None

    def delete_collage_asset(self, name: str):
        try:
            p = self._collage_assets_dir() / Path(name).name
            if p.exists():
                p.unlink()
        except OSError:
            pass

    # ── Wallpaper ──────────────────────────────────────────────────────────────

    def save_wallpaper(self, data: bytes, ext: str) -> Path:
        """Save wallpaper image bytes. ext should be 'jpg', 'png', 'webp', etc."""
        ext = ext.lstrip('.').lower() or 'jpg'
        p = _data_dir() / f'wallpaper.{ext}'
        # Remove any old wallpaper with a different extension
        for old in _data_dir().glob('wallpaper.*'):
            if old.name != p.name:
                try:
                    old.unlink()
                except OSError:
                    pass
        _write_bytes(p, data)
        return p

    def get_wallpaper_path(self) -> Path | None:
        """Return the path to the stored wallpaper, or None if none exists."""
        for p in _data_dir().glob('wallpaper.*'):
            if p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.avif', '.gif'):
                return p
        return None

    def clear_wallpaper(self):
        """Delete the stored wallpaper."""
        for p in _data_dir().glob('wallpaper.*'):
            try:
                p.unlink()
            except OSError:
                pass


    # ── Backup & restore (whole library) ────────────────────────────────────
    # A backup is a single .lemurlib zip containing everything in the data dir
    # (library index, settings, collections, wallpaper, and every book's files
    # + annotation versions), under a data/ prefix, plus a small manifest.
    _BACKUP_DIRNAME = 'backups'
    _BACKUP_KEEP    = 8          # rolling auto-snapshots to retain

    def get_data_dir(self) -> str:
        return str(_data_dir())

    def _iter_backup_files(self):
        """Yield (abs_path, arcname) for everything to include in a backup,
        excluding the backups folder itself and stray atomic-write temp files."""
        root = _data_dir()
        backups = root / self._BACKUP_DIRNAME
        for p in root.rglob('*'):
            if not p.is_file():
                continue
            if p.suffix == '.tmp':
                continue
            if backups == p or backups in p.parents:
                continue
            yield p, p.relative_to(root).as_posix()

    def export_library(self) -> bytes:
        """Zip the entire data directory into a single .lemurlib archive."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('lemur-backup.json', json.dumps({
                'app': 'Lemur', 'kind': 'library-backup', 'format': 1,
                'created': int(time.time()), 'books': len(self.get_library()),
            }, ensure_ascii=False))
            for abs_path, arc in self._iter_backup_files():
                try:
                    zf.write(abs_path, 'data/' + arc)
                except OSError:
                    pass
        return buf.getvalue()

    def _snapshot(self, label: str = 'auto'):
        """Write a timestamped safety backup into backups/ and prune to the most
        recent _BACKUP_KEEP. Best-effort: never raises into the caller."""
        try:
            bdir = _data_dir() / self._BACKUP_DIRNAME
            bdir.mkdir(parents=True, exist_ok=True)
            ts   = time.strftime('%Y%m%d-%H%M%S')
            _write_bytes(bdir / f'{ts}-{label}.lemurlib', self.export_library())
            for old in sorted(bdir.glob('*.lemurlib'))[:-self._BACKUP_KEEP]:
                try: old.unlink()
                except OSError: pass
        except Exception:
            pass

    def list_backups(self) -> list:
        """The rolling auto-snapshots, newest first: [{name, size, created}]."""
        bdir = _data_dir() / self._BACKUP_DIRNAME
        out  = []
        if bdir.exists():
            for p in sorted(bdir.glob('*.lemurlib'), reverse=True):
                try:
                    st = p.stat()
                    out.append({'name': p.name, 'size': st.st_size,
                                'created': int(st.st_mtime)})
                except OSError:
                    pass
        return out

    def read_backup(self, name: str) -> bytes | None:
        """Read one auto-snapshot by name (used to restore it). Name is matched
        against the backups folder only — never an arbitrary path."""
        p = (_data_dir() / self._BACKUP_DIRNAME / Path(name).name)
        return p.read_bytes() if p.exists() and p.suffix == '.lemurlib' else None

    def import_library(self, zip_bytes: bytes, mode: str = 'merge') -> dict:
        """Restore from a .lemurlib archive. Always snapshots current data first.

        mode='merge'  → add/overwrite books, union the library & collections,
                        and KEEP this machine's settings/wallpaper/watched dirs.
        mode='replace'→ wipe current data (except backups) and restore fully."""
        import shutil
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except Exception:
            return {'success': False, 'error': 'Not a valid backup file.'}
        names = set(zf.namelist())
        if 'lemur-backup.json' not in names:
            return {'success': False, 'error': 'This file is not a Lemur library backup.'}

        self._snapshot('pre-restore')
        root    = _data_dir()
        members = [n for n in names if n.startswith('data/') and not n.endswith('/')]

        def _extract(n):
            dest = root / n[len('data/'):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes(dest, zf.read(n))

        if mode == 'replace':
            for child in root.iterdir():
                if child.name == self._BACKUP_DIRNAME:
                    continue
                try:
                    shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
                except OSError:
                    pass
            for n in members:
                _extract(n)
            return {'success': True, 'mode': 'replace', 'books': len(self.get_library())}

        # ── merge ──
        for n in members:                       # restore every book's files
            if n.startswith('data/books/'):
                _extract(n)
        for n in members:                       # restore collage image assets
            if n.startswith('data/collage-assets/'):
                _extract(n)
        if 'data/collages.json' in names:        # union boards + tray by id
            incoming = _read_json_bytes(zf.read('data/collages.json'), {}) or {}
            cur      = self.get_collages()
            for fld in ('boards', 'tray'):
                have = {x.get('id') for x in cur.get(fld, [])}
                for x in (incoming.get(fld) or []):
                    if x.get('id') and x.get('id') not in have:
                        cur[fld].append(x)
            self.save_collages(cur)
        if 'data/library.json' in names:        # union library by id (keep existing)
            incoming = _read_json_bytes(zf.read('data/library.json'), []) or []
            cur      = self.get_library()
            have     = {e.get('id') for e in cur}
            for e in incoming:
                if e.get('id') and e.get('id') not in have:
                    cur.append(e)
            _write_json(root / 'library.json', cur)
        if 'data/collections.json' in names:    # union collections by id
            incoming = _read_json_bytes(zf.read('data/collections.json'), []) or []
            cur      = self.get_collections()
            have     = {c.get('id') for c in cur}
            for c in incoming:
                if c.get('id') and c.get('id') not in have:
                    cur.append(c)
            self.save_collections(cur)
        return {'success': True, 'mode': 'merge', 'books': len(self.get_library())}


store = DataStore()
