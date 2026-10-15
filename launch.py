"""
Lemur — standalone launcher
Starts Flask in a background thread, then opens the app in a native
WebView2 window (Edge engine, pre-installed on Windows 11).
"""

import threading
import time
import sys
import os
import ctypes
import ctypes.wintypes as _wt
import webview

sys.path.insert(0, os.path.dirname(__file__))
from app import app as flask_app

# ── Flask ──────────────────────────────────────────────────────────────────
def run_flask():
    flask_app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

threading.Thread(target=run_flask, daemon=True).start()

import urllib.request
for _ in range(40):
    try:
        urllib.request.urlopen('http://127.0.0.1:5000', timeout=1)
        break
    except Exception:
        time.sleep(0.1)

# ═══════════════════════════════════════════════════════════════════════════
#  Frameless-window maximize & JS-driven resize
#
#  Resize  : inject capture-phase JS listeners that call set_window_rect via
#            pywebview.api.  No WS_THICKFRAME needed — SetWindowPos handles
#            everything and never confuses WebView2's drag/hit-test logic.
#
#  Maximize: SetWindowPos to the monitor work-area (SW_SHOWMAXIMIZED on
#            WS_POPUP covers the taskbar and doesn't restore cleanly).
#
#  NOTE: WS_THICKFRAME was previously added here to enable OS resize loops,
#  but it caused WebView2 to reclassify the entire window as a "chrome window"
#  and route all pointer events through non-client hit-testing — making every
#  left- AND right-click drag the window instead of interacting with content.
#  The style change is intentionally absent.
# ═══════════════════════════════════════════════════════════════════════════

_u32    = ctypes.windll.user32
_OUR_PID = os.getpid()

_SWP_NOZORDER   = 0x0004
_SWP_NOSIZE     = 0x0001
_SWP_NOACTIVATE = 0x0010

class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize',    ctypes.c_ulong),
                ('rcMonitor', _wt.RECT),
                ('rcWork',    _wt.RECT),
                ('dwFlags',   ctypes.c_ulong)]

_maximized_set = set()
_saved_rects   = {}   # hwnd → (x, y, w, h) before maximize


def _work_area(hwnd):
    hmon = _u32.MonitorFromWindow(hwnd, 2)   # MONITOR_DEFAULTTONEAREST
    mi   = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    _u32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi.rcWork


def _toggle_maximize(hwnd):
    """Toggle maximized state using SetWindowPos to the monitor work-area."""
    if hwnd in _maximized_set:
        _maximized_set.discard(hwnd)
        if hwnd in _saved_rects:
            x, y, w, h = _saved_rects.pop(hwnd)
            _u32.SetWindowPos(hwnd, 0, x, y, w, h,
                              _SWP_NOZORDER | _SWP_NOACTIVATE)
        return False
    else:
        rc = _wt.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(rc))
        _saved_rects[hwnd] = (rc.left, rc.top,
                              rc.right - rc.left, rc.bottom - rc.top)
        wa = _work_area(hwnd)
        _u32.SetWindowPos(hwnd, 0,
                          wa.left, wa.top,
                          wa.right - wa.left, wa.bottom - wa.top,
                          _SWP_NOZORDER | _SWP_NOACTIVATE)
        _maximized_set.add(hwnd)
        return True


# ── Pure-poll edge resize ──────────────────────────────────────────────────
#
#  WH_MOUSE_LL hooks in Python are fundamentally unsafe: every mouse event
#  (hundreds per second) calls a Python function, which must acquire the GIL.
#  If any other Python thread holds the GIL — even for a few milliseconds —
#  the hook stalls, Windows times it out, and the cursor freezes.
#
#  Solution: no hook at all.  A background thread polls GetAsyncKeyState +
#  GetCursorPos at 120 Hz.  It cannot freeze mouse input under any
#  circumstance because it has nothing to do with the Windows hook chain.
#
#  Trade-off: the initial edge click reaches WebView2, but since there is
#  no content within 6 px of the window border the browser starts an empty
#  selection that vanishes on release — invisible to the user.

_RESIZE_B      = 6
_MIN_W, _MIN_H = 380, 300

_rs = {'active': False, 'dir': None, 'hwnd': None,
       'sx': 0, 'sy': 0, 'ox': 0, 'oy': 0, 'ow': 0, 'oh': 0}

# Drag state — populated by LemurAPI.drag_start(), driven by poll thread
_drag_rs = {'active': False, 'hwnd': None,
            'sx': 0, 'sy': 0, 'ox': 0, 'oy': 0}


def _hwnd_of_ours():
    """Root HWND of the foreground window if it belongs to our process."""
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return None
    root = _u32.GetAncestor(hwnd, 2)   # GA_ROOT
    if root:
        hwnd = root
    pid = _wt.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd if pid.value == _OUR_PID else None


def _resize_dir(x, y, hwnd):
    """Which resize direction is (x, y) in, relative to hwnd's border?"""
    rc = _wt.RECT()
    if not _u32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return None
    L = x <  rc.left   + _RESIZE_B
    R = x >= rc.right  - _RESIZE_B
    B = y >= rc.bottom - _RESIZE_B
    # Top edge deliberately excluded: overlaps the -webkit-app-region:drag
    # title bar, which already handles dragging from that zone.
    if not (L or R or B):
        return None
    if B and L: return 'sw'
    if B and R: return 'se'
    if B: return 's'
    if L: return 'w'
    return 'e'


def _resize_poll():
    """
    Polls at 120 Hz.  Drives both edge-resize and title-bar drag.

    Resize: detected here by checking cursor vs. window edge on fresh click.
    Drag:   JS calls pywebview.api.drag_start() which populates _drag_rs;
            this thread tracks cursor deltas and calls SetWindowPos.
    """
    _VK_LBUTTON = 0x01
    _prev_down  = False

    while True:
        time.sleep(1 / 120)

        cur_down = bool(_u32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000)

        # ── Active resize ──────────────────────────────────────────────────
        if _rs['active']:
            if not cur_down:
                _rs['active'] = False
                _rs['hwnd']   = None
            else:
                pt = _wt.POINT()
                _u32.GetCursorPos(ctypes.byref(pt))
                dx = pt.x - _rs['sx']
                dy = pt.y - _rs['sy']
                d  = _rs['dir']
                nx, ny = _rs['ox'], _rs['oy']
                nw, nh = _rs['ow'], _rs['oh']
                if 'e' in d: nw = _rs['ow'] + dx
                if 's' in d: nh = _rs['oh'] + dy
                if 'w' in d: nx = _rs['ox'] + dx;  nw = _rs['ow'] - dx
                nw = max(nw, _MIN_W);  nh = max(nh, _MIN_H)
                try:
                    _u32.SetWindowPos(_rs['hwnd'], 0, nx, ny, nw, nh,
                                      _SWP_NOZORDER | _SWP_NOACTIVATE)
                except Exception:
                    pass

        # ── Active drag ────────────────────────────────────────────────────
        elif _drag_rs['active']:
            if not cur_down:
                _drag_rs['active'] = False
                _drag_rs['hwnd']   = None
            else:
                pt = _wt.POINT()
                _u32.GetCursorPos(ctypes.byref(pt))
                nx = _drag_rs['ox'] + (pt.x - _drag_rs['sx'])
                ny = _drag_rs['oy'] + (pt.y - _drag_rs['sy'])
                try:
                    _u32.SetWindowPos(_drag_rs['hwnd'], 0, nx, ny, 0, 0,
                                      _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_NOSIZE)
                except Exception:
                    pass

        # ── Fresh click — check for edge resize ────────────────────────────
        elif cur_down and not _prev_down:
            pt = _wt.POINT()
            _u32.GetCursorPos(ctypes.byref(pt))
            x, y = pt.x, pt.y
            hwnd = _hwnd_of_ours()
            if hwnd and hwnd not in _maximized_set:
                d = _resize_dir(x, y, hwnd)
                if d:
                    rc = _wt.RECT()
                    _u32.GetWindowRect(hwnd, ctypes.byref(rc))
                    _rs.update(active=True, dir=d, hwnd=hwnd,
                               sx=x, sy=y,
                               ox=rc.left, oy=rc.top,
                               ow=rc.right  - rc.left,
                               oh=rc.bottom - rc.top)

        _prev_down = cur_down


threading.Thread(target=_resize_poll, daemon=True).start()


# ── Panel window registry ──────────────────────────────────────────────────
_panel_windows = {}

def _panel_closed(panel_name):
    _panel_windows.pop(panel_name, None)

# ── Python API exposed to JavaScript ──────────────────────────────────────
class LemurAPI:
    def open_panel(self, url, width, height, panel_name, title):
        existing = _panel_windows.get(panel_name)
        if existing is not None:
            try:
                existing.evaluate_js('window.focus()')
            except Exception:
                pass
            return 'focused'
        try:
            w = webview.create_window(
                title            = f'Lemur — {title}',
                url              = url,
                width            = int(width)  if width  else 900,
                height           = int(height) if height else 700,
                min_size         = (380, 300),
                resizable        = True,
                frameless        = True,
                easy_drag        = False,
                background_color = '#0e0e0f',
                js_api           = api,
            )
            _panel_windows[panel_name] = w
            w.events.loaded += lambda: _inject(w)
            w.events.closed += lambda: _panel_closed(panel_name)
            return 'opened'
        except Exception as e:
            print(f'[Lemur] open_panel error: {e}')
            return f'error:{e}'

    def panel_closed(self, panel_name):
        _panel_windows.pop(panel_name, None)
        return True

    def is_panel_open(self, panel_name):
        return panel_name in _panel_windows

    def pick_folder(self):
        """Open a native folder-picker dialog. Returns the selected path or None."""
        try:
            result = main_window.create_file_dialog(
                webview.FOLDER_DIALOG, allow_multiple=False)
            if result and len(result) > 0:
                return result[0]
        except Exception as e:
            print(f'[Lemur] pick_folder error: {e}')
        return None

    def drag_start(self):
        """
        Called by JS when the user presses the left button on the title bar.
        Records the cursor anchor and initial window position so the poll
        thread can drive the move without further JS involvement.

        Cursor position is read here via GetCursorPos (physical pixels) rather
        than being passed from JS (which uses CSS/logical pixels).  Mixing the
        two coordinate spaces causes an immediate snap on DPI-scaled displays.
        """
        try:
            hwnd = _u32.GetForegroundWindow()
            if not hwnd:
                return
            root = _u32.GetAncestor(hwnd, 2)
            if root:
                hwnd = root
            pt = _wt.POINT()
            _u32.GetCursorPos(ctypes.byref(pt))
            rc = _wt.RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(rc))
            _drag_rs.update(active=True, hwnd=hwnd,
                            sx=pt.x, sy=pt.y,
                            ox=rc.left, oy=rc.top)
        except Exception as e:
            print(f'[Lemur] drag_start error: {e}')

    def window_cmd(self, cmd):
        """
        Called from the custom title-bar buttons in any frameless window.
        cmd: 'minimize' | 'togglemax' | 'close'
        Returns True if the window is now maximised (only meaningful for 'togglemax').
        """
        try:
            hwnd = _u32.GetForegroundWindow()
            if not hwnd:
                return False

            # GetForegroundWindow may return a WebView2 child HWND — walk up
            # to the true top-level pywebview frame.
            root = _u32.GetAncestor(hwnd, 2)   # GA_ROOT = 2
            if root:
                hwnd = root

            if cmd == 'minimize':
                _u32.ShowWindow(hwnd, 6)                    # SW_MINIMIZE
                return False
            elif cmd == 'close':
                _u32.PostMessageW(hwnd, 0x0010, 0, 0)       # WM_CLOSE
                return False
            elif cmd == 'togglemax':
                return _toggle_maximize(hwnd)

        except Exception as e:
            print(f'[Lemur] window_cmd error: {e}')
        return False


api = LemurAPI()

# ── JS injected after every page load ─────────────────────────────────────
INJECT_JS = r"""
(function() {
    if (window.__srPywebviewPatched) return;
    window.__srPywebviewPatched = true;

    var TITLES = {illus:'Illustration', sidebar:'Sidebar', reader:'Reader'};

    window.open = function(url, name, features) {
        try {
            var abs     = new URL(url, location.href);
            var panel   = abs.searchParams.get('panel') || 'panel';
            var title   = TITLES[panel] || 'Panel';
            var fw      = parseInt((features||'').match(/width=(\d+)/)?.[1])  || 900;
            var fh      = parseInt((features||'').match(/height=(\d+)/)?.[1]) || 700;

            pywebview.api.open_panel(abs.href, fw, fh, name || panel, title);

            var mock = { closed: false, _name: name || panel,
                focus: function() { pywebview.api.open_panel(abs.href, fw, fh, name||panel, title); },
                close: function() { this.closed = true; pywebview.api.panel_closed(this._name); }
            };

            var interval = setInterval(function() {
                pywebview.api.is_panel_open(mock._name).then(function(open) {
                    if (!open) { mock.closed = true; clearInterval(interval); }
                });
            }, 800);

            return mock;
        } catch(e) {
            console.error('[Lemur] open_panel failed:', e);
            return null;
        }
    };

    console.log('[Lemur] pywebview window.open patch applied');

    // ── Frameless-window edge resize — cursor feedback only ───────────────────
    //
    // Resize is handled entirely by a WH_MOUSE_LL Python hook that intercepts
    // mouse events BEFORE WebView2 sees them.  The hook eats edge left-clicks
    // and calls SetWindowPos directly; WebView2 never sees those events, so
    // text selection, right-click bookmarking and all other interactions are
    // completely unaffected.
    //
    // The only JS-side work is showing the appropriate resize cursor when the
    // pointer hovers near an edge.  No mousedown interception here at all.
    (function() {
        var _B   = 6;    // resize zone in CSS px from each edge
        var _ALL = ['n','s','e','w','nw','ne','sw','se'];
        var _cur = null; // currently shown cursor direction

        // Inject cursor-class styles once
        if (!document.getElementById('sr-rs-style')) {
            var s = document.createElement('style');
            s.id = 'sr-rs-style';
            s.textContent =
                'html.sr-rs-n,html.sr-rs-n *{cursor:n-resize!important}' +
                'html.sr-rs-s,html.sr-rs-s *{cursor:s-resize!important}' +
                'html.sr-rs-e,html.sr-rs-e *{cursor:e-resize!important}' +
                'html.sr-rs-w,html.sr-rs-w *{cursor:w-resize!important}' +
                'html.sr-rs-nw,html.sr-rs-nw *{cursor:nw-resize!important}' +
                'html.sr-rs-ne,html.sr-rs-ne *{cursor:ne-resize!important}' +
                'html.sr-rs-sw,html.sr-rs-sw *{cursor:sw-resize!important}' +
                'html.sr-rs-se,html.sr-rs-se *{cursor:se-resize!important}';
            document.head.appendChild(s);
        }

        function _dir(cx, cy) {
            var W = document.documentElement.clientWidth;
            var H = document.documentElement.clientHeight;
            var E = cx >= W-_B, W_ = cx < _B, S = cy >= H-_B, N = cy < _B;
            if (!E && !W_ && !S && !N) return null;
            if (N && W_) return 'nw'; if (N && E) return 'ne';
            if (S && W_) return 'sw'; if (S && E) return 'se';
            if (N) return 'n';        if (S) return 's';
            if (W_) return 'w';       return 'e';
        }

        function _setCursor(d) {
            if (d === _cur) return;
            _ALL.forEach(function(x) { document.documentElement.classList.remove('sr-rs-'+x); });
            if (d) document.documentElement.classList.add('sr-rs-'+d);
            _cur = d;
        }

        // Cursor feedback only — resize is driven by the Python poll thread
        document.addEventListener('mousemove', function(ev) {
            _setCursor(_dir(ev.clientX, ev.clientY));
        }, true);
    })();

    // ── Title-bar drag ────────────────────────────────────────────────────────
    //
    // easy_drag=False disables pywebview's whole-window drag listener so that
    // clicking anywhere in the content area does NOT move the window.
    // We re-implement dragging here, restricted to non-interactive areas inside
    // elements that have -webkit-app-region:drag in their computed style.
    //
    // On mousedown on a draggable area: call pywebview.api.drag_start() once.
    // The Python poll thread then moves the window at 120 Hz based on
    // GetCursorPos deltas until the button is released — no further JS needed.
    //
    // Walk-up approach: a child element may have computed no-drag (from CSS like
    // "#header * { no-drag }") even though its parent has drag.  We walk up the
    // ancestor chain until we hit a drag region (allow) or a button/input (deny).
    (function() {
        document.addEventListener('mousedown', function(ev) {
            if (ev.button !== 0) return;

            // Walk up from ev.target looking for a -webkit-app-region:drag ancestor.
            // Stop (deny) immediately on interactive elements.
            var el = ev.target;
            var isDrag = false;
            while (el && el !== document.documentElement) {
                if (el.matches('button,input,a,select,textarea,[role="button"]')) {
                    isDrag = false;
                    break;
                }
                var r = (window.getComputedStyle(el)
                             .getPropertyValue('-webkit-app-region') || '').trim();
                if (r === 'drag') { isDrag = true; break; }
                // 'no-drag' on a child does not block us from checking ancestors —
                // the pattern "#header { drag } #header * { no-drag }" is common.
                el = el.parentElement;
            }
            if (!isDrag) return;

            // Prevent text-selection ghost during the drag
            ev.preventDefault();

            // No coordinates passed — Python reads GetCursorPos() itself so
            // everything stays in physical pixels (avoids DPI-scaling snap).
            if (typeof pywebview !== 'undefined' && pywebview.api) {
                pywebview.api.drag_start();
            }
        }, false);   // bubble phase — interactive children can stopPropagation
    })();
})();
"""

def _inject(win):
    try:
        win.evaluate_js(INJECT_JS)
    except Exception as e:
        print(f'[Lemur] JS inject error: {e}')

def on_loaded():
    _inject(main_window)

# ── Main window ────────────────────────────────────────────────────────────
main_window = webview.create_window(
    title            = 'Lemur',
    url              = 'http://127.0.0.1:5000',
    width            = 1400,
    height           = 900,
    min_size         = (900, 600),
    resizable        = True,
    frameless        = True,
    easy_drag        = False,       # whole-window drag disabled; topbar drag is in JS
    background_color = '#0e0e0f',   # matches --bg; prevents white flash on resize
    js_api           = api,
)

main_window.events.loaded += on_loaded

webview.start(debug=False, private_mode=False)
