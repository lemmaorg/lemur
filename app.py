from flask import Flask, render_template, request, jsonify, send_file
import fitz  # PyMuPDF
import os
import io
import sys
import json
import time
import base64
import zipfile
from pathlib import Path
from store import store, safe_id, _data_dir
import i18n

# ── Resource base path ──────────────────────────────────────────────────────
# When frozen by PyInstaller the app runs from a temporary extraction dir
# (sys._MEIPASS); during normal development it runs from this file's folder.
# Flask must be pointed at the bundled templates/ and static/ explicitly,
# otherwise it looks next to the (non-existent) source module and 404s.
if getattr(sys, 'frozen', False):
    _BASE_DIR = Path(sys._MEIPASS)
else:
    _BASE_DIR = Path(__file__).parent

app = Flask(
    __name__,
    template_folder=str(_BASE_DIR / 'templates'),
    static_folder=str(_BASE_DIR / 'static'),
)


# ── Local-only request guard ────────────────────────────────────────────────
# The backend listens on 127.0.0.1 with no login, so the threat is not the
# network but OTHER WEBSITES the user visits in a normal browser: they could
# submit forms to our port (CSRF) or, via DNS rebinding, read the library.
# Every legitimate request comes from Lemur's own page (same origin) or from
# Electron's main process, so we reject anything else:
#   1. Host must be a loopback name — a DNS-rebinding attack arrives under the
#      attacker's hostname, so this blocks it outright.
#   2. Browsers label requests made by other sites with Sec-Fetch-Site; refuse
#      cross-site AND same-site (localhost vs 127.0.0.1 counts as a different
#      site, and Lemur's page only ever talks to its own origin).
#   3. For state-changing methods, an Origin that isn't ours (including the
#      "null" origin of sandboxed frames and local HTML files) is refused —
#      this covers browsers that don't send Sec-Fetch-Site.
_LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1', '[::1]'}
_STATE_CHANGING = {'POST', 'PUT', 'PATCH', 'DELETE'}


def _request_rejection():
    """Return a reason string if the current request must be refused."""
    host = (request.host or '').lower()
    hostname = host[:host.rindex(']') + 1] if host.startswith('[') else host.split(':')[0]
    if hostname not in _LOOPBACK_HOSTS:
        return 'host'
    if request.headers.get('Sec-Fetch-Site', '').lower() in ('cross-site', 'same-site'):
        return 'cross-site'
    if request.method in _STATE_CHANGING:
        origin = request.headers.get('Origin')
        if origin is not None and origin.rstrip('/') != request.host_url.rstrip('/'):
            return 'origin'
    return None


@app.before_request
def _guard_local_only():
    reason = _request_rejection()
    if reason:
        return jsonify({'success': False, 'error': 'Forbidden', 'reason': reason}), 403

# Keep last uploaded PDF in memory so /page-image can serve pages on demand.
_doc = None

# Track the current book ID (matches store key) so API routes can find it.
_current_book_id = None

RENDER_SCALE    = 2.0   # render at 2× for crisp display on HiDPI; word positions use fractions
THUMB_TARGET_W  = 720   # target pixel width for generated thumbnails
THUMB_MIN_WIDTH = 500   # cached thumbs narrower than this are regenerated automatically

_SUPPORTED_EXTS = {'.pdf', '.epub', '.epub.zip', '.zip'}


def _open_doc(file_bytes: bytes, filename: str = '') -> tuple:
    """Open a fitz Document from raw bytes.
    Returns (fitz.Document, file_type) where file_type is 'pdf' or 'epub'.

    EPUB detection:
      • '.epub' extension  → always epub
      • '.epub.zip'        → epub distributed as a zip (common download format)
      • ZIP magic bytes (PK\\x03\\x04) on any other name  → try epub, fall back to pdf
    """
    name_lower = filename.lower()
    # Explicit epub extensions
    if name_lower.endswith('.epub') or name_lower.endswith('.epub.zip'):
        return fitz.open(stream=file_bytes, filetype='epub'), 'epub'
    # Unknown extension but ZIP magic bytes — attempt epub first
    if name_lower.endswith('.zip') and file_bytes[:4] == b'PK\x03\x04':
        try:
            doc = fitz.open(stream=file_bytes, filetype='epub')
            return doc, 'epub'
        except Exception:
            pass
    return fitz.open(stream=file_bytes, filetype='pdf'), 'pdf'


def _jpeg_width(data: bytes) -> int:
    """Parse image width from a JPEG SOF segment without any extra dependencies.
    Returns 0 on parse failure (treated as 'too small → regenerate')."""
    i = 2  # skip SOI marker (FF D8)
    try:
        while i + 8 < len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):   # SOF0, SOF1, SOF2
                return (data[i + 7] << 8) | data[i + 8]
            seg_len = (data[i + 2] << 8) | data[i + 3]
            i += 2 + seg_len
    except (IndexError, ValueError):
        pass
    return 0

# ── Optional OCR support (requires: pip install pytesseract pillow
#    and Tesseract installed on the system) ────────────────────────
try:
    import pytesseract
    from PIL import Image
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


# ── Reading-order helpers ──────────────────────────────────────────────────

def _detect_column_split(raw_words, page_width):
    """
    Find the x-coordinate of the vertical gutter separating two text columns,
    or None for single-column pages.

    Detection scans each central x-strip for the longest run of consecutive
    lines in which NO word covers it. A two-column gutter stays empty down the
    whole body, so it shows a long empty run — even when a full-width header
    (title/authors/abstract) sits above the columns (MIXED page), and even when
    the two columns aren't baseline-aligned (we test coverage per line, not a
    left+right gap on the same line). A single column's centre is covered on
    almost every line, so it has no long empty run.
    """
    if not raw_words or page_width <= 0:
        return None
    if len(raw_words) < 40:            # too little text to trust a split
        return None

    lines = sorted(_group_into_lines(raw_words), key=lambda l: min(w[1] for w in l))
    if len(lines) < 8:
        return None

    n_bins   = 80
    bin_size = page_width / n_bins
    # For each line, which x-bins does any word cover?
    line_cov = []
    for line in lines:
        covered = [False] * n_bins
        for w in line:
            b0 = max(0, int(w[0] / bin_size))
            b1 = min(n_bins - 1, int(w[2] / bin_size))
            for b in range(b0, b1 + 1):
                covered[b] = True
        line_cov.append(covered)

    # Longest run of consecutive lines that leave each central bin empty.
    lo, hi = int(n_bins * 0.28), int(n_bins * 0.72)
    best_run, best_bin = 0, None
    for b in range(lo, hi + 1):
        run = 0
        for cov in line_cov:
            if not cov[b]:
                run += 1
                if run > best_run:
                    best_run, best_bin = run, b
            else:
                run = 0

    # The gutter must stay empty across a large share of the body's lines.
    if best_bin is None or best_run < len(lines) * 0.35:
        return None

    split = (best_bin + 0.5) * bin_size

    # Substantial text must sit on BOTH sides of the gutter (rules out a single
    # column with a wide/centred indent or a one-sided layout).
    left = sum(1 for w in raw_words if (w[0] + w[2]) / 2 < split)
    if min(left, len(raw_words) - left) < len(raw_words) * 0.25:
        return None

    return split


def _group_into_lines(words):
    """
    Group a list of word tuples into visual lines using vertical-overlap clustering.
    Returns a list of lines, each line being a list of word tuples.
    """
    lines = []
    for word in sorted(words, key=lambda w: w[1]):   # top-to-bottom pass
        y0, y1 = word[1], word[3]
        h      = max(y1 - y0, 1)
        placed = False
        for line in lines:
            ly0 = min(w[1] for w in line)
            ly1 = max(w[3] for w in line)
            lh  = max(ly1 - ly0, h)
            overlap = min(y1, ly1) - max(y0, ly0)
            if overlap / lh > 0.3:          # ≥30 % overlap → same line
                line.append(word)
                placed = True
                break
        if not placed:
            lines.append([word])
    return lines


def _sort_column(words):
    """Sort words in a single column: lines top-to-bottom, words left-to-right."""
    result = []
    for line in sorted(_group_into_lines(words), key=lambda l: min(w[1] for w in l)):
        result.extend(sorted(line, key=lambda w: w[0]))
    return result


def sort_words_reading_order(raw_words, page_width):
    """
    Re-order raw PyMuPDF word tuples into natural human reading order.
    Handles both single-column and two-column page layouts.
    """
    if not raw_words:
        return raw_words

    split = _detect_column_split(raw_words, page_width)

    if split is None:
        return _sort_column(raw_words)

    return _order_two_column(raw_words, split, page_width)


def _order_two_column(raw_words, split, page_width):
    """
    Reading order for a page with a gutter at x=`split`, handling MIXED pages
    (full-width header/figures interleaved with two-column body):

    Walk lines top-to-bottom. A line whose text runs continuously across the
    gutter (only a normal word-space at `split`) is FULL-WIDTH — emit it in
    place (this keeps a spanning title intact instead of splitting it). A line
    with a real column-gap at the gutter is a two-column row — its left half is
    buffered as a left-column line and its right half as a right-column line.
    Buffered columns are flushed (all left, then all right) whenever a
    full-width line interrupts them and once more at the end — so each column is
    read fully before the next.
    """
    gutter_gap = page_width * 0.05    # a real column gap is wider than this
    lines = sorted(_group_into_lines(raw_words), key=lambda l: min(w[1] for w in l))

    result   = []
    buf_left = []
    buf_right = []

    def flush():
        for ln in buf_left:
            result.extend(sorted(ln, key=lambda w: w[0]))
        for ln in buf_right:
            result.extend(sorted(ln, key=lambda w: w[0]))
        buf_left.clear()
        buf_right.clear()

    for line in lines:
        ws    = sorted(line, key=lambda w: w[0])
        left  = [w for w in ws if (w[0] + w[2]) / 2 <  split]
        right = [w for w in ws if (w[0] + w[2]) / 2 >= split]
        # Two-column row only if both halves exist AND there's a real gap at the
        # gutter between them; otherwise the text crosses the gutter → full-width.
        if left and right and (right[0][0] - left[-1][2]) >= gutter_gap:
            buf_left.append(left)
            buf_right.append(right)
        elif left and right:
            flush()                       # full-width line interrupts the columns
            result.extend(ws)
        elif left:
            buf_left.append(left)
        else:
            buf_right.append(right)

    flush()
    return result


def _ocr_page(page):
    """
    OCR a single PyMuPDF page using Tesseract and return word tuples in the
    same format as page.get_text('words'):
        (x0, y0, x1, y1, word_text, block_no, line_no, word_no)
    Coordinates are in PDF-point space (not pixels).
    Returns an empty list if OCR is unavailable or fails.
    """
    if not _OCR_AVAILABLE:
        return []

    # OCR is a best-effort enhancement for image-only / scanned pages. It must
    # NEVER abort opening a book, so any failure here returns [] (the page keeps
    # whatever text it already had). In particular, EPUB reflow can produce
    # blank or zero-dimension pages whose render is empty — feeding that to
    # Tesseract makes Pillow raise "cannot write empty image".
    try:
        from PIL import ImageEnhance, ImageFilter

        # Render at 3× for better OCR accuracy on small/stylised text
        OCR_SCALE = 3.0
        mat = fitz.Matrix(OCR_SCALE, OCR_SCALE)
        pix = page.get_pixmap(matrix=mat)

        # Skip empty/zero-size renders — nothing to OCR, and saving them throws.
        if not pix.width or not pix.height or not pix.samples:
            return []

        mode = "RGBA" if pix.alpha else "RGB"
        pil_img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        if mode == "RGBA":
            pil_img = pil_img.convert("RGB")

        # Greyscale + contrast boost + sharpening
        gray = pil_img.convert("L")
        gray = ImageEnhance.Contrast(gray).enhance(2.0)
        gray = gray.filter(ImageFilter.SHARPEN)

        data = pytesseract.image_to_data(
            gray,
            output_type=pytesseract.Output.DICT,
            config="--oem 3 --psm 3",
        )
    except Exception as e:
        print(f"[ocr] skipped page (OCR failed): {e}")
        return []

    words = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text or int(data["conf"][i]) < 20:
            continue
        x0 = data["left"][i]                       / OCR_SCALE
        y0 = data["top"][i]                        / OCR_SCALE
        x1 = (data["left"][i] + data["width"][i])  / OCR_SCALE
        y1 = (data["top"][i]  + data["height"][i]) / OCR_SCALE
        words.append((
            x0, y0, x1, y1, text,
            data["block_num"][i],
            data["line_num"][i],
            data["word_num"][i],
        ))

    return words


@app.route("/")
def index():
    # Inject persisted settings so the page can seed localStorage synchronously
    # before any script reads it — keeps UI prefs stable regardless of port.
    settings = store.get_settings()
    settings_json = json.dumps(settings, ensure_ascii=False)
    # Inject the active UI language + its merged strings so the page can apply
    # translations synchronously on load (no flash of English).
    # In debug, drop the locale cache each load so edits to locale/*.json show
    # immediately (the Flask reloader doesn't watch JSON files). Production keeps
    # the cache — locales never change at runtime there.
    if app.debug:
        i18n.clear_cache()
    lang = (settings.get('sr_lang') or 'en').strip() or 'en'
    i18n_blob = json.dumps({
        'lang': lang,
        'rtl': i18n.is_rtl(lang),
        'strings': i18n.load_locale(lang),
    }, ensure_ascii=False)
    return render_template("index.html", injected_settings=settings_json,
                           injected_i18n=i18n_blob)


@app.route("/api/i18n/languages")
def api_i18n_languages():
    """List the languages with a locale file present (for the Settings picker)."""
    return jsonify(i18n.available_languages())


@app.route("/healthz")
def healthz():
    """Tiny readiness probe for the Electron launcher — cheap to hit while the
    backend is still warming up (avoids downloading the full page repeatedly)."""
    return "ok", 200


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(store.get_settings())


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    """Set one key (body: {key, value}) or merge a whole dict (body: {settings})."""
    data = request.get_json(silent=True) or {}
    if "settings" in data and isinstance(data["settings"], dict):
        # Merge, never replace: this bulk push can race the per-key writes the
        # page fires at startup, and a replace would wipe keys they just saved.
        store.merge_settings(data["settings"])
    elif "key" in data:
        store.set_setting(data["key"], data.get("value"))
    return jsonify({"success": True})


# ── Collage (cross-book scrapbook notebook) ─────────────────────────────────

@app.route("/api/collages", methods=["GET"])
def api_get_collages():
    return jsonify(store.get_collages())


@app.route("/api/collages", methods=["POST"])
def api_save_collages():
    data = request.get_json(silent=True) or {}
    payload = data.get("collages") if "collages" in data else data
    store.save_collages(payload if isinstance(payload, dict) else {})
    return jsonify({"success": True})


@app.route("/api/collage/asset", methods=["POST"])
def api_save_collage_asset():
    """Snapshot an image into permanent collage storage. Accepts a data URL in
    body {data: 'data:image/png;base64,...'}. Returns {assetId}."""
    data = request.get_json(silent=True) or {}
    durl = data.get("data") or ""
    ext = "png"
    b64 = durl
    if durl.startswith("data:"):
        try:
            head, b64 = durl.split(",", 1)
            if "image/jpeg" in head or "image/jpg" in head:
                ext = "jpg"
            elif "image/webp" in head:
                ext = "webp"
        except ValueError:
            return jsonify({"success": False, "error": "bad data url"}), 400
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return jsonify({"success": False, "error": "bad base64"}), 400
    if not raw or len(raw) > 25 * 1024 * 1024:
        return jsonify({"success": False, "error": "empty or too large"}), 400
    name = store.save_collage_asset(raw, ext)
    return jsonify({"success": True, "assetId": name})


@app.route("/api/collage-asset/<name>")
def api_get_collage_asset(name):
    raw = store.get_collage_asset(name)
    if raw is None:
        return ("", 404)
    ext = (name.rsplit(".", 1)[-1] or "png").lower()
    mt = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
          "webp": "image/webp"}.get(ext, "image/png")
    return send_file(io.BytesIO(raw), mimetype=mt)


# ── Data safety: whole-library backup & restore ─────────────────────────────

@app.route("/api/data/info")
def api_data_info():
    """Where the library lives + the rolling auto-snapshots available."""
    return jsonify({"dir": store.get_data_dir(),
                    "backups": store.list_backups()})


@app.route("/api/data/backup")
def api_data_backup():
    """Download the entire library as a single .lemurlib archive."""
    blob  = store.export_library()
    fname = "Lemur-Library-" + time.strftime("%Y%m%d") + ".lemurlib"
    return send_file(io.BytesIO(blob), mimetype="application/octet-stream",
                     as_attachment=True, download_name=fname)


@app.route("/api/data/restore", methods=["POST"])
def api_data_restore():
    """Restore from an uploaded .lemurlib (multipart 'file') or a named
    auto-snapshot ('snapshot' form field). 'mode' is 'merge' (default) or
    'replace'. A pre-restore safety snapshot is always taken first."""
    mode = (request.form.get("mode") or request.args.get("mode") or "merge").lower()
    if mode not in ("merge", "replace"):
        mode = "merge"

    blob = None
    up = request.files.get("file")
    if up is not None:
        blob = up.read()
    else:
        snap = request.form.get("snapshot")
        if snap:
            blob = store.read_backup(snap)
    if not blob:
        return jsonify({"success": False, "error": "No backup file provided."}), 400

    result = store.import_library(blob, mode=mode)
    code = 200 if result.get("success") else 400
    return jsonify(result), code


def _process_pdf_doc(doc) -> dict:
    """
    Extract words, pages, and TOC from an open fitz.Document.
    Returns the dict that /upload (and /api/book/open) send to the frontend.
    """
    all_words  = []
    pages_meta = []
    ocr_page_count = 0

    for page_num, page in enumerate(doc):
        rect = page.rect
        raw  = page.get_text("words")

        real_words = [w for w in raw if w[4].strip()]
        if len(real_words) < 3 and _OCR_AVAILABLE:
            ocr_raw = _ocr_page(page)
            if ocr_raw:
                raw = ocr_raw
                ocr_page_count += 1

        raw = sort_words_reading_order(raw, rect.width)

        page_word_start = len(all_words)
        page_words = []

        for w in raw:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
            block_no, line_no = int(w[5]), int(w[6])
            text = text.strip()
            if not text:
                continue
            page_words.append({
                "i":  len(all_words),
                "t":  text,
                "xf": round(x0 / rect.width,  4),
                "yf": round(y0 / rect.height, 4),
                "wf": round((x1 - x0) / rect.width,  4),
                "hf": round((y1 - y0) / rect.height, 4),
                "b":  block_no,
                "l":  line_no,
            })
            all_words.append(text)

        # Embedded hyperlinks (PDF & EPUB). Stored as fractional rects so the
        # frontend can overlay click hotspots on the rendered page image at any
        # zoom. We expose two kinds: external web/mail URIs ("uri") and in-book
        # page jumps ("page", 0-based). javascript:/file:/etc. are dropped.
        lw = rect.width or 1
        lh = rect.height or 1
        page_links = []
        for ln in page.get_links():
            r = ln.get("from")
            if r is None:
                continue
            entry = {
                "xf": round(r.x0 / lw, 4),
                "yf": round(r.y0 / lh, 4),
                "wf": round((r.x1 - r.x0) / lw, 4),
                "hf": round((r.y1 - r.y0) / lh, 4),
            }
            uri = (ln.get("uri") or "").strip()
            if uri:
                if uri.lower().startswith(("http://", "https://", "mailto:")):
                    entry["uri"] = uri
                else:
                    continue
            elif ln.get("kind") == fitz.LINK_GOTO and ln.get("page", -1) >= 0:
                entry["page"] = int(ln["page"])   # 0-based target page
            else:
                continue
            page_links.append(entry)

        pages_meta.append({
            "p":  page_num,
            "w":  round(rect.width),
            "h":  round(rect.height),
            "ws": page_word_start,
            "words": page_words,
            "links": page_links,
        })

    toc_raw = doc.get_toc()
    toc = [{"level": item[0], "title": item[1], "page": item[2]} for item in toc_raw]

    return {
        "success":       True,
        "word_count":    len(all_words),
        "words":         all_words,
        "pages":         pages_meta,
        "toc":           toc,
        "ocr_pages":     ocr_page_count,
        "ocr_available": _OCR_AVAILABLE,
    }


@app.route("/upload", methods=["POST"])
def upload_pdf():
    """Accept a PDF or EPUB upload.  The file field may be named 'pdf' or 'epub'."""
    global _doc, _current_book_id
    try:
        file = (request.files.get('pdf') or
                request.files.get('epub') or
                request.files.get('file'))
        if not file:
            return jsonify({'success': False, 'error': 'No file provided'}), 400

        filename   = file.filename or ''
        file_bytes = file.read()
        book_id    = safe_id(filename or 'book')

        if _doc:
            _doc.close()
        _doc, file_type = _open_doc(file_bytes, filename)
        _current_book_id = book_id

        result = _process_pdf_doc(_doc)

        # Persist book file and update library entry
        if file_type == 'epub':
            store.save_epub(book_id, file_bytes)
        else:
            store.save_pdf(book_id, file_bytes)

        store.upsert_library({
            'id':        book_id,
            'name':      filename or book_id,
            'wordCount': result['word_count'],
            'pageCount': len(result['pages']),
            'lastOpened': int(time.time() * 1000),
            'fileType':  file_type,
        })

        result['book_id'] = book_id
        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/page-image/<int:page_num>")
def page_image(page_num):
    global _doc
    if _doc is None:
        return "No PDF loaded", 404
    if page_num < 0 or page_num >= len(_doc):
        return "Page out of range", 404
    try:
        page = _doc[page_num]
        mat  = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
        pix  = page.get_pixmap(matrix=mat)
        if pix.width and pix.height:
            img = pix.tobytes("jpeg", jpg_quality=88)
        else:
            # Some EPUBs (esp. image-only ones) paginate blank/zero-size filler
            # pages whose render is empty. Serve a clean blank page sized to the
            # page's logical box instead of erroring out (which showed as a
            # broken thin bar in page view).
            import io
            r = page.rect
            w = int((r.width  or 420) * RENDER_SCALE)
            h = int((r.height or 560) * RENDER_SCALE)
            w = max(40, min(w, 2400)); h = max(40, min(h, 3200))
            buf = io.BytesIO()
            Image.new("RGB", (w, h), "white").save(buf, "JPEG", quality=70)
            img = buf.getvalue()
        resp = app.response_class(img, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "max-age=3600"
        return resp
    except Exception as e:
        return str(e), 500


def _gap_has_content(page, clip_rect, scale=0.15, min_dark_ratio=0.03):
    """Render a tiny grayscale sample of clip_rect and check for non-white pixels.
    Returns True if at least min_dark_ratio of pixels are darker than 230/255.
    Fast: at 15 % scale a 400×100 region becomes 60×15 = 900 bytes to scan."""
    try:
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, clip=clip_rect, colorspace=fitz.csGRAY)
        samples = pix.samples
        if not samples:
            return False
        n_dark = sum(1 for b in samples if b < 230)
        return (n_dark / len(samples)) >= min_dark_ratio
    except Exception:
        return False


def _merge_near_rects(rects, tol):
    """Union rectangles whose (slightly inflated) boxes touch or overlap, so a
    figure that the file exposes as several adjacent image tiles/parts collapses
    into one region. Figures separated by more than `tol` stay distinct."""
    boxes = [[r.x0, r.y0, r.x1, r.y1] for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        for b in boxes:
            placed = False
            for o in out:
                if (b[0] <= o[2] + tol and b[2] >= o[0] - tol and
                        b[1] <= o[3] + tol and b[3] >= o[1] - tol):
                    o[0] = min(o[0], b[0]); o[1] = min(o[1], b[1])
                    o[2] = max(o[2], b[2]); o[3] = max(o[3], b[3])
                    placed = True; changed = True
                    break
            if not placed:
                out.append(list(b))
        boxes = out
    return [fitz.Rect(*b) for b in boxes]


def _find_illustrations(page, min_ratio=0.04):
    """Return list of all significant figures on the page, sorted by area descending.
    Each entry: {idx, ratio, side, rect_norm:[x0,y0,x1,y1]} with coords normalised 0-1.

    Three-pass strategy:
      Pass 1 — page.get_images() + get_image_bbox().
                Works reliably for PDF XObject images.
      Pass 2 — page.get_text("blocks") image blocks (block_type == 1).
                Covers EPUB/SVG formats where images are inline fitz blocks.
      Pass 3 — Text-gap analysis + pixel validation.
                Some EPUBs (e.g. the Biostar Handbook) render images visually
                but expose them through none of the fitz image APIs.  They appear
                as vertical gaps in text block coverage.  We find those gaps,
                require a minimum height, and confirm with a low-res pixel sample
                that the gap contains non-white content before accepting it.
    """
    rect      = page.rect
    page_area = rect.width * rect.height
    candidates  = []
    seen_bboxes = []

    def _accept(bbox):
        """Return (area, ratio) if the bbox passes size/overlap filters, else None."""
        if not bbox or bbox.is_empty:
            return None
        w, h  = bbox.x1 - bbox.x0, bbox.y1 - bbox.y0
        area  = w * h
        ratio = area / page_area
        if ratio < min_ratio or w < 40 or h < 40:
            return None
        dup = any(
            max(0, min(bbox.x1, s.x1) - max(bbox.x0, s.x0)) *
            max(0, min(bbox.y1, s.y1) - max(bbox.y0, s.y0)) > 0.8 * area
            for s in seen_bboxes
        )
        return None if dup else (area, ratio)

    # ── Pass 1: XObject images (PDF native) ───────────────────────────────
    for img in page.get_images(full=True):
        try:
            bbox = page.get_image_bbox(img)
            hit  = _accept(bbox)
            if hit is None:
                continue
            area, ratio = hit
            seen_bboxes.append(bbox)
            cx = (bbox.x0 + bbox.x1) / 2 / rect.width
            candidates.append({
                'ratio': round(ratio, 3),
                'side':  'left' if cx < 0.5 else 'right',
                '_area': area,
                '_bbox': bbox,
            })
        except Exception:
            continue

    # ── Pass 2: inline image blocks (EPUB / reflowable formats) ───────────
    if not candidates:
        try:
            for block in page.get_text("blocks"):
                if len(block) < 7 or int(block[6]) != 1:
                    continue
                bbox = fitz.Rect(block[0], block[1], block[2], block[3])
                hit  = _accept(bbox)
                if hit is None:
                    continue
                area, ratio = hit
                seen_bboxes.append(bbox)
                cx = (bbox.x0 + bbox.x1) / 2 / rect.width
                candidates.append({
                    'ratio': round(ratio, 3),
                    'side':  'left' if cx < 0.5 else 'right',
                    '_area': area,
                    '_bbox': bbox,
                })
        except Exception:
            pass

    # ── Pass 3: text-gap analysis + pixel validation ───────────────────────
    # Collect vertical extents of all text blocks, merge overlapping spans,
    # then inspect every gap between consecutive spans.  A gap that is:
    #   • tall enough  (≥ 50 px — filters out heading/paragraph spacing)
    #   • big enough   (≥ min_ratio of page area)
    #   • contains non-white pixels in a low-res render sample
    # is accepted as an illustration region.
    if not candidates:
        try:
            y_spans = []
            for block in page.get_text("blocks"):
                if len(block) >= 4 and (len(block) < 7 or int(block[6]) == 0):
                    y_spans.append((float(block[1]), float(block[3])))

            if len(y_spans) >= 2:
                y_spans.sort()
                # Merge spans that overlap or touch (2 px tolerance)
                merged = [list(y_spans[0])]
                for y0, y1 in y_spans[1:]:
                    if y0 <= merged[-1][1] + 2:
                        merged[-1][1] = max(merged[-1][1], y1)
                    else:
                        merged.append([y0, y1])

                for i in range(len(merged) - 1):
                    gap_y0 = merged[i][1]
                    gap_y1 = merged[i + 1][0]
                    if gap_y1 - gap_y0 < 50:
                        continue          # heading gap / line spacing — skip

                    gap_rect = fitz.Rect(0, gap_y0, rect.width, gap_y1)
                    hit = _accept(gap_rect)
                    if hit is None:
                        continue

                    if not _gap_has_content(page, gap_rect):
                        continue          # empty whitespace — skip

                    area, ratio = hit
                    seen_bboxes.append(gap_rect)
                    cx = (gap_rect.x0 + gap_rect.x1) / 2 / rect.width
                    candidates.append({
                        'ratio': round(ratio, 3),
                        'side':  'left' if cx < 0.5 else 'right',
                        '_area': area,
                        '_bbox': gap_rect,
                    })
        except Exception:
            pass

    # ── Merge adjacent parts of the same figure ───────────────────────────
    # Files often expose one figure/table as several image tiles (or an image +
    # its caption-region). Union boxes that touch/nearly-touch so they render as
    # a single illustration instead of unstructured fragments.
    if len(candidates) > 1:
        tol = min(rect.width, rect.height) * 0.02
        candidates = []
        for bbox in _merge_near_rects([c['_bbox'] for c in candidates], tol):
            area  = (bbox.x1 - bbox.x0) * (bbox.y1 - bbox.y0)
            ratio = area / page_area
            cx    = (bbox.x0 + bbox.x1) / 2 / rect.width
            candidates.append({
                'ratio': round(ratio, 3),
                'side':  'left' if cx < 0.5 else 'right',
                '_area': area,
                '_bbox': bbox,
            })

    candidates.sort(key=lambda c: -c['_area'])
    # The margin added by page_illus_img so draw-coord mapping matches the rendered image.
    margin = min(rect.width, rect.height) * 0.015
    mx, my = margin / rect.width, margin / rect.height
    result = []
    for i, c in enumerate(candidates):
        x0n = round(c['_bbox'].x0 / rect.width,  4)
        y0n = round(c['_bbox'].y0 / rect.height, 4)
        x1n = round(c['_bbox'].x1 / rect.width,  4)
        y1n = round(c['_bbox'].y1 / rect.height, 4)
        result.append({
            'idx':       i,
            'ratio':     c['ratio'],
            'side':      c['side'],
            'rect_norm': [x0n, y0n, x1n, y1n],
            # render_norm = the actual crop region used by page_illus_img (includes margin).
            # JS must use this as _illustCurrentRegion so drawing coords align exactly.
            'render_norm': [
                round(max(0.0, x0n - mx), 4),
                round(max(0.0, y0n - my), 4),
                round(min(1.0, x1n + mx), 4),
                round(min(1.0, y1n + my), 4),
            ],
        })
    return result


@app.route("/page-illus-meta/<int:page_num>")
def page_illus_meta(page_num):
    """Returns JSON with all significant figures on the page."""
    global _doc
    if _doc is None or page_num < 0 or page_num >= len(_doc):
        return jsonify({"found": False, "figures": []})
    try:
        page    = _doc[page_num]
        figures = _find_illustrations(page)
        if not figures:
            return jsonify({"found": False, "figures": []})
        primary = figures[0]
        # Keep top-level found/side/ratio for backward-compat with existing JS checks
        return jsonify({
            "found":   True,
            "side":    primary['side'],
            "ratio":   primary['ratio'],
            "figures": figures,
        })
    except Exception as e:
        return jsonify({"found": False, "figures": [], "error": str(e)})



@app.route("/page-illus-img/<int:page_num>")
@app.route("/page-illus-img/<int:page_num>/<int:idx>")
def page_illus_img(page_num, idx=0):
    """Renders the idx-th figure on the page as a JPEG (idx=0 = largest)."""
    global _doc
    if _doc is None or page_num < 0 or page_num >= len(_doc):
        return "No PDF loaded", 404
    try:
        page    = _doc[page_num]
        figures = _find_illustrations(page)
        if not figures or idx >= len(figures):
            return "No illustration", 404
        fig  = figures[idx]
        prect = page.rect
        x0n, y0n, x1n, y1n = fig['rect_norm']
        x0, y0 = x0n * prect.width,  y0n * prect.height
        x1, y1 = x1n * prect.width,  y1n * prect.height
        margin = min(prect.width, prect.height) * 0.015
        clip = fitz.Rect(
            max(0,           x0 - margin),
            max(0,           y0 - margin),
            min(prect.width,  x1 + margin),
            min(prect.height, y1 + margin),
        )
        mat = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
        pix = page.get_pixmap(matrix=mat, clip=clip)
        img = pix.tobytes("jpeg", jpg_quality=90)
        resp = app.response_class(img, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "max-age=3600"
        return resp
    except Exception as e:
        return str(e), 500


@app.route("/page-region-img/<int:page_num>/<float:x0n>/<float:y0n>/<float:x1n>/<float:y1n>")
def page_region_img(page_num, x0n, y0n, x1n, y1n):
    """Renders a normalised-coordinate (0-1) region of the page at high resolution."""
    global _doc
    if _doc is None or page_num < 0 or page_num >= len(_doc):
        return "No PDF loaded", 404
    try:
        page  = _doc[page_num]
        prect = page.rect
        x0 = max(0, min(prect.width,  x0n * prect.width))
        y0 = max(0, min(prect.height, y0n * prect.height))
        x1 = max(0, min(prect.width,  x1n * prect.width))
        y1 = max(0, min(prect.height, y1n * prect.height))
        if x1 <= x0 or y1 <= y0:
            return "Invalid region", 400
        clip = fitz.Rect(x0, y0, x1, y1)
        mat  = fitz.Matrix(RENDER_SCALE * 2, RENDER_SCALE * 2)   # 4× effective DPI
        pix  = page.get_pixmap(matrix=mat, clip=clip)
        img  = pix.tobytes("jpeg", jpg_quality=92)
        resp = app.response_class(img, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "max-age=3600"
        return resp
    except Exception as e:
        return str(e), 500


# ═══════════════════════════════════════════════════════════════════════════
#  Library / persistence API
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/library")
def api_library():
    lib = store.get_library()
    # hasPdf is kept as the key name for frontend compat; true for PDF and EPUB
    for e in lib:
        e['hasPdf'] = store.has_book(e.get('id', ''))
    return jsonify(lib)


@app.route("/api/book/save", methods=["POST"])
def api_book_save():
    """Persist all user data for a book (debounced from JS)."""
    global _current_book_id
    data    = request.json or {}
    book_id = data.get('id') or _current_book_id
    if not book_id:
        return jsonify({'success': False, 'error': 'no book id'}), 400

    for key in ('notes', 'bookmarks', 'drawings', 'notedraw', 'mindmap', 'illusbms', 'theme'):
        if key in data:
            store.save_data(book_id, key, data[key])

    if 'libraryEntry' in data:
        entry = data['libraryEntry']
        entry.setdefault('id', book_id)
        store.upsert_library(entry)

    return jsonify({'success': True})


@app.route("/api/book/<book_id>")
def api_book_get(book_id):
    """Return all saved user data for a book."""
    d = store.get_all_data(book_id)
    d['hasPdf'] = store.has_book(book_id)
    return jsonify(d)


@app.route("/api/book/open/<book_id>")
def api_book_open(book_id):
    """
    Open a book from the stored file copy (PDF or EPUB — no file picker needed).
    Returns the same payload as /upload.
    """
    global _doc, _current_book_id
    book_bytes, file_type = store.get_book(book_id)
    if not book_bytes:
        return jsonify({'success': False, 'error': 'No stored file for this book'}), 404
    try:
        if _doc:
            _doc.close()
        _doc = fitz.open(stream=book_bytes, filetype=file_type)
        _current_book_id = book_id

        result = _process_pdf_doc(_doc)
        result['book_id']   = book_id
        result['versions']  = store.get_versions(book_id)
        result['activeVid'] = result['versions']['activeId']
        result['book_data'] = store.get_all_data(book_id)

        # Update lastOpened
        lib = store.get_library()
        entry = next((e for e in lib if e.get('id') == book_id), None)
        if entry:
            entry['lastOpened'] = int(time.time() * 1000)
            store.upsert_library(entry)

        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Annotation versions ────────────────────────────────────────────────────

@app.route("/api/book/versions/<book_id>", methods=["GET"])
def api_versions_list(book_id):
    return jsonify(store.get_versions(book_id))


@app.route("/api/book/versions/<book_id>", methods=["POST"])
def api_versions_create(book_id):
    """Create a new version. body: {name, data?, makeActive?}."""
    d = request.get_json(silent=True) or {}
    vid = store.add_version(book_id, d.get('name', 'Untitled'),
                            data=d.get('data'),
                            make_active=d.get('makeActive', True))
    return jsonify({'success': True, 'vid': vid, 'versions': store.get_versions(book_id)})


@app.route("/api/book/versions/<book_id>/active", methods=["POST"])
def api_versions_set_active(book_id):
    """Switch active version. body: {vid}. Returns that version's data."""
    d = request.get_json(silent=True) or {}
    vid = d.get('vid')
    if not store.set_active_version(book_id, vid):
        return jsonify({'success': False, 'error': 'unknown version'}), 404
    return jsonify({'success': True, 'activeVid': vid,
                    'book_data': store.get_all_data(book_id, vid),
                    'versions': store.get_versions(book_id)})


@app.route("/api/book/versions/<book_id>/rename", methods=["POST"])
def api_versions_rename(book_id):
    d = request.get_json(silent=True) or {}
    store.rename_version(book_id, d.get('vid'), d.get('name', ''))
    return jsonify({'success': True, 'versions': store.get_versions(book_id)})


@app.route("/api/book/versions/<book_id>/delete", methods=["POST"])
def api_versions_delete(book_id):
    d = request.get_json(silent=True) or {}
    ok = store.delete_version(book_id, d.get('vid'))
    return jsonify({'success': ok, 'versions': store.get_versions(book_id),
                    'book_data': store.get_all_data(book_id)})


@app.route("/api/book/remove/<book_id>", methods=["POST"])
def api_book_remove(book_id):
    """Remove a book from the library index (does not delete stored files)."""
    store.remove_from_library(book_id)
    return jsonify({'success': True})


@app.route("/api/book/delete/<book_id>", methods=["POST"])
def api_book_delete(book_id):
    """Remove from the library AND delete all stored data + the stored book
    file (the whole book directory). The user's original source file on disk
    is untouched."""
    store.delete_book(book_id)
    return jsonify({'success': True})


@app.route("/api/book/reset/<book_id>", methods=["POST"])
def api_book_reset(book_id):
    """Delete a book's annotations (notes/bookmarks/drawings/mindmap/etc.) but
    keep the book in the library and its stored file — a per-book 'reset'."""
    store.reset_book_data(book_id)
    return jsonify({'success': True})


@app.route("/api/book/clear-pdf/<book_id>", methods=["POST"])
def api_book_clear_pdf(book_id):
    """
    Delete the stored book file (PDF or EPUB) while keeping all user data.
    Used to recover from a corrupted copy — the user can then re-upload.
    """
    from pathlib import Path
    try:
        bd = store._book_dir(book_id)
        for fname in ('book.pdf', 'book.epub', 'thumb.jpg'):
            p = bd / fname
            if p.exists():
                p.unlink()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/api/export/<book_id>")
def api_export(book_id):
    """Download a .srbook project file containing the PDF + all saved data.

    Query params:
        no_pdf=1   — produce an annotations-only archive (no PDF).
        pos=N      — reading position (currentIndex) to embed in meta.json.
    """
    global _doc
    include_pdf = request.args.get('no_pdf', '0') != '1'

    # Extra fields for meta.json
    extra_meta = {}
    try:
        pos = request.args.get('pos')
        if pos is not None:
            extra_meta['currentIndex'] = int(pos)
    except (ValueError, TypeError):
        pass

    # Determine file type from library so we handle epub differently from pdf
    lib   = store.get_library()
    entry = next((e for e in lib if e.get('id') == book_id), {})
    file_type = entry.get('fileType', 'pdf')

    # For PDF: use the live in-memory doc (captures any unsaved drawing changes).
    # For EPUB: fitz cannot round-trip to epub format — use the stored original.
    doc_bytes = None
    if include_pdf and _doc is not None and _current_book_id == book_id:
        if file_type == 'pdf':
            buf = io.BytesIO()
            _doc.save(buf)
            doc_bytes = buf.getvalue()
        else:
            doc_bytes = store.get_epub(book_id)

    # Optional: a specific version, and a category filter (comma-separated).
    vid = request.args.get('vid') or None
    cats_param = request.args.get('cats')
    categories = [c for c in cats_param.split(',') if c] if cats_param else None

    try:
        zip_bytes = store.export_srbook(book_id, doc_bytes,
                                        include_pdf=include_pdf,
                                        extra_meta=extra_meta,
                                        file_type=file_type,
                                        vid=vid, categories=categories)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    clean_name = (entry.get('name') or book_id)
    for ext in ('.pdf', '.epub'):
        clean_name = clean_name.replace(ext, '')
    fname = clean_name + '.srbook'

    return send_file(
        io.BytesIO(zip_bytes),
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=fname,
    )


@app.route("/api/import", methods=["POST"])
def api_import():
    """
    Import a .srbook archive.
    Loads the embedded PDF into memory, restores all data, and returns
    the same payload as /upload so the frontend can treat it identically.

    Query params:
        force=1  — overwrite an existing library entry without prompting.
    """
    global _doc, _current_book_id
    try:
        f = request.files.get('srbook')
        if not f:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400

        raw = f.read()
        # Optional name for the imported version (the frontend can prompt for one).
        version_name = (request.form.get('version_name') or '').strip() or None

        # Imports ALWAYS land in a new named version — never overwriting an
        # existing book's annotations — so no conflict prompt is needed.
        book_id, meta, book_bytes, file_type, vid = store.import_srbook(
            raw, version_name=version_name)
        _current_book_id = book_id

        if book_bytes:
            if _doc:
                _doc.close()
            _doc = fitz.open(stream=book_bytes, filetype=file_type or 'pdf')
            result = _process_pdf_doc(_doc)
        else:
            result = {'success': True, 'word_count': 0, 'words': [],
                      'pages': [], 'toc': [], 'ocr_pages': 0,
                      'ocr_available': _OCR_AVAILABLE}

        result['book_id']   = book_id
        result['book_data'] = store.get_all_data(book_id, vid)
        result['meta']      = meta
        result['versions']  = store.get_versions(book_id)
        result['activeVid'] = vid
        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  Collections API
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/collections", methods=["GET"])
def api_collections_list():
    return jsonify(store.get_collections())


@app.route("/api/collections", methods=["POST"])
def api_collections_create():
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'name required'}), 400
    col = store.add_collection(name)
    return jsonify({'success': True, 'collection': col})


@app.route("/api/collections/<col_id>", methods=["PATCH"])
def api_collections_update(col_id):
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'name required'}), 400
    ok = store.update_collection(col_id, name)
    return jsonify({'success': ok})


@app.route("/api/collections/<col_id>", methods=["DELETE"])
def api_collections_delete(col_id):
    store.delete_collection(col_id)
    return jsonify({'success': True})


@app.route("/api/collections/<col_id>/books", methods=["PUT"])
def api_collections_set_books(col_id):
    data = request.get_json(force=True) or {}
    book_ids = data.get('bookIds', [])
    ok = store.set_collection_books(col_id, book_ids)
    return jsonify({'success': ok})


# ═══════════════════════════════════════════════════════════════════════════
#  Watched directories + thumbnail API
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/watched-dirs")
def api_watched_dirs_get():
    return jsonify(store.get_watched_dirs())


@app.route("/api/watched-dirs/add", methods=["POST"])
def api_watched_dirs_add():
    data = request.json or {}
    path = (data.get('path') or '').strip()
    if not path or not os.path.isdir(path):
        return jsonify({'success': False, 'error': 'Not a valid directory'}), 400
    dirs = store.add_watched_dir(path)
    return jsonify({'success': True, 'dirs': dirs})


@app.route("/api/watched-dirs/remove", methods=["POST"])
def api_watched_dirs_remove():
    data = request.json or {}
    path = data.get('path', '')
    dirs = store.remove_watched_dir(path)
    return jsonify({'success': True, 'dirs': dirs})


@app.route("/api/watched-dirs/scan")
def api_watched_dirs_scan():
    results = store.scan_watched_dirs()
    return jsonify(results)


@app.route("/api/book/thumb/<book_id>")
def api_book_thumb(book_id):
    """
    Serve a small JPEG thumbnail of page 0.
    Checks the on-disk cache first; generates and caches on the first request.
    For un-copied PDFs pass ?path=<url-encoded-absolute-path>.
    """
    cached = store.get_thumb(book_id)
    if cached and _jpeg_width(cached) >= THUMB_MIN_WIDTH:
        resp = app.response_class(cached, mimetype='image/jpeg')
        resp.headers['Cache-Control'] = 'max-age=86400'
        return resp

    # Try the stored book copy first (PDF or EPUB), then the supplied path
    book_bytes, file_type = store.get_book(book_id)
    if not book_bytes:
        path = request.args.get('path', '')
        if path and os.path.isfile(path):
            try:
                with open(path, 'rb') as f:
                    book_bytes = f.read()
                _, ext = os.path.splitext(path)
                file_type = 'epub' if ext.lower() == '.epub' else 'pdf'
            except OSError:
                pass

    if not book_bytes:
        return 'No book file available', 404

    try:
        doc   = fitz.open(stream=book_bytes, filetype=file_type or 'pdf')
        page  = doc[0]
        scale = THUMB_TARGET_W / page.rect.width   # fit to target width regardless of page size
        mat   = fitz.Matrix(scale, scale)
        pix   = page.get_pixmap(matrix=mat)
        jpeg  = pix.tobytes('jpeg', jpg_quality=88)
        doc.close()
        store.save_thumb(book_id, jpeg)
        resp = app.response_class(jpeg, mimetype='image/jpeg')
        resp.headers['Cache-Control'] = 'max-age=86400'
        return resp
    except Exception as e:
        return str(e), 500


@app.route("/api/book/open-path", methods=["POST"])
def api_book_open_path():
    """
    Open a PDF or EPUB directly from a filesystem path (watched-dir files that
    have never been uploaded through the normal picker).
    Behaves like /upload: processes words and saves a copy to the library.
    """
    global _doc, _current_book_id
    data = request.json or {}
    path = (data.get('path') or '').strip()
    if not path or not os.path.isfile(path):
        return jsonify({'success': False, 'error': 'File not found'}), 404

    path_lower = path.lower()
    if not any(path_lower.endswith(e) for e in _SUPPORTED_EXTS):
        _, ext = os.path.splitext(path)
        return jsonify({'success': False, 'error': f'Unsupported file type: {ext}'}), 400

    try:
        with open(path, 'rb') as f:
            file_bytes = f.read()

        name    = os.path.basename(path)
        book_id = safe_id(name)

        if _doc:
            _doc.close()
        _doc, file_type = _open_doc(file_bytes, name)
        _current_book_id = book_id

        result = _process_pdf_doc(_doc)
        result['book_id'] = book_id

        if file_type == 'epub':
            store.save_epub(book_id, file_bytes)
        else:
            store.save_pdf(book_id, file_bytes)

        store.upsert_library({
            'id':        book_id,
            'name':      name,
            'path':      path,
            'wordCount': result['word_count'],
            'pageCount': len(result['pages']),
            'lastOpened': int(time.time() * 1000),
            'fileType':  file_type,
        })

        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  Wallpaper API
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/wallpaper", methods=["GET", "HEAD"])
def api_wallpaper_get():
    path = store.get_wallpaper_path()
    if not path:
        return '', 404
    ext = path.suffix.lstrip('.').lower()
    mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'
    if request.method == 'HEAD':
        return app.response_class('', mimetype=mime)
    return send_file(str(path), mimetype=mime)


@app.route("/api/wallpaper", methods=["POST"])
def api_wallpaper_post():
    f = request.files.get('wallpaper')
    if not f:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    fname = f.filename or 'wallpaper.jpg'
    ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else 'jpg'
    store.save_wallpaper(f.read(), ext)
    return jsonify({'success': True})


@app.route("/api/wallpaper", methods=["DELETE"])
def api_wallpaper_delete():
    store.clear_wallpaper()
    return jsonify({'success': True})


@app.route("/api/about")
def api_about():
    """Return app metadata shown in the Settings → About panel."""
    try:
        pkg_json = _BASE_DIR / 'package.json'
        version  = json.loads(pkg_json.read_text(encoding='utf-8')).get('version', '—')
    except Exception:
        version = '—'
    return jsonify({
        'version':  version,
        'dataDir':  str(_data_dir()),
        'userName': store.get_user_name(),
    })


if __name__ == "__main__":
    # When launched by Electron, disable debug mode and the reloader so only
    # one Flask process runs (debug=True spawns a second reloader process).
    electron_mode = os.environ.get("LEMUR_ELECTRON") == "1"
    # Electron passes the port it reserved via LEMUR_PORT so we never fight
    # over a fixed port (avoids "port 5000 in use" startup failures). Falls
    # back to 5000 for standalone/dev runs.
    port = int(os.environ.get("LEMUR_PORT") or os.environ.get("PORT") or "5000")
    app.run(debug=not electron_mode, use_reloader=not electron_mode, port=port)
