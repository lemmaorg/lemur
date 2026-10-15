"""
Lemur UI internationalization (i18n).

Translations live as flat JSON files in locale/<code>.json — a dict of
{ "key": "text" }. English (en.json) is the source of truth and the fallback;
any key missing from another language falls back to its English string, so the
UI never shows a blank.

The frontend uses t('key') for JS strings and data-i18n="key" attributes for
static HTML. The active language is stored in settings as 'sr_lang'.
"""
import json
import sys
from pathlib import Path

# Resolve locale/ both in dev and inside a PyInstaller one-folder bundle, where
# data files are unpacked under sys._MEIPASS rather than next to the source.
if getattr(sys, 'frozen', False):
    _BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
else:
    _BASE = Path(__file__).parent
_LOCALE_DIR = _BASE / 'locale'

# Native names for the languages we know about. Only codes that actually have a
# locale/<code>.json file are offered in the picker — this is just the label.
_LANG_NAMES = {
    'en': 'English',
    'es': 'Español',
    'fr': 'Français',
    'de': 'Deutsch',
    'pt': 'Português',
    'it': 'Italiano',
    'nl': 'Nederlands',
    'pl': 'Polski',
    'ru': 'Русский',
    'ja': '日本語',
    'ko': '한국어',
    'zh': '中文',
    'ar': 'العربية',
    'hi': 'हिन्दी',
    'bn': 'বাংলা',
    'ur': 'اردو',
    'tr': 'Türkçe',
    'sr': 'Српски',
}

# Languages that read right-to-left (the frontend sets dir="rtl" for these).
_RTL = {'ar', 'he', 'fa', 'ur'}

_cache: dict = {}


def _read(code: str) -> dict:
    p = _LOCALE_DIR / f'{code}.json'
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def available_languages() -> list:
    """[{code, name, rtl}] for every locale/<code>.json present, English first."""
    out = []
    if _LOCALE_DIR.exists():
        for p in sorted(_LOCALE_DIR.glob('*.json')):
            code = p.stem
            out.append({
                'code': code,
                'name': _LANG_NAMES.get(code, code),
                'rtl': code in _RTL,
            })
    # Ensure English is present and first.
    out.sort(key=lambda d: (d['code'] != 'en', d['name'].lower()))
    return out


def load_locale(code: str) -> dict:
    """English strings overlaid with the requested language (missing keys fall
    back to English). Cached per code."""
    code = (code or 'en').strip() or 'en'
    if code in _cache:
        return _cache[code]
    base = _read('en')
    merged = base if code == 'en' else {**base, **_read(code)}
    _cache[code] = merged
    return merged


def is_rtl(code: str) -> bool:
    return (code or 'en') in _RTL


def clear_cache():
    """Drop cached locales (call after editing a JSON file in dev)."""
    _cache.clear()
