# -*- coding: utf-8 -*-
"""
wfmix_i18n - translations via GNU gettext.

Source language is English. Translations live in
    locale/<lang>/LC_MESSAGES/wfmix.po
and are shared by the Python code (``_()``) and the web UI (served as JSON).

The .po files are compiled to .mo at start (pure Python, no msgfmt needed)
into ~/.cache/wfmix/locale and loaded with the standard ``gettext`` module.
The language can be switched at runtime (the UI has a language selector).

Adding a language: copy locale/wfmix.pot to locale/<lang>/LC_MESSAGES/wfmix.po,
translate it (e.g. with Poedit), restart wfmix.
"""

import ast
import gettext
import os
import struct
import threading

DOMAIN = "wfmix"
HERE = os.path.dirname(os.path.abspath(__file__))
LOCALE_DIR = os.path.join(HERE, "locale")
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "wfmix", "locale")

_lock = threading.Lock()
_translations = {}          # lang -> gettext.NullTranslations
_current = threading.local()
_default = {"lang": "en"}


# --------------------------------------------------------------------------
# .po lesen / .mo schreiben (Teilmenge des Formats: msgctxt, msgid, msgstr,
# Fortsetzungszeilen, fuzzy-Markierung; keine Pluralformen noetig)
# --------------------------------------------------------------------------

def _unquote(s):
    s = s.strip()
    if not (s.startswith('"') and s.endswith('"')):
        raise ValueError("bad po string: %r" % s)
    return ast.literal_eval(s)


def parse_po(path):
    """Liefert {msgid: msgstr} (Header unter "") ohne fuzzy/unuebersetzte Eintraege.
    Eintraege sind - wie in .po-Dateien ueblich - durch Leerzeilen getrennt."""
    with open(path, encoding="utf-8") as fp:
        blocks = fp.read().replace("\r\n", "\n").split("\n\n")
    entries = {}
    for block in blocks:
        fields = {}
        cur = None
        fuzzy = False
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("#,"):
                fuzzy = "fuzzy" in line
            elif line.startswith("#"):
                continue
            elif line.startswith(("msgctxt ", "msgid ", "msgstr ")):
                cur, rest = line.split(" ", 1)
                fields[cur] = _unquote(rest)
            elif line.startswith('"') and cur:
                fields[cur] += _unquote(line)
        if "msgid" not in fields or "msgstr" not in fields or fuzzy:
            continue
        if not fields["msgstr"] and fields["msgid"]:
            continue
        key = fields["msgid"]
        if "msgctxt" in fields:
            key = fields["msgctxt"] + "\x04" + key
        entries[key] = fields["msgstr"]
    return entries


def write_mo(entries, path):
    """Schreibt eine GNU .mo-Datei (Format siehe gettext-Dokumentation)."""
    keys = sorted(entries)
    ids = b""
    strs = b""
    offsets = []
    for k in keys:
        kb, vb = k.encode("utf-8"), entries[k].encode("utf-8")
        offsets.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b"\0"
        strs += vb + b"\0"
    n = len(keys)
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + len(ids)
    koffsets, voffsets = [], []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    data = struct.pack("Iiiiiii", 0x950412de, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    data += struct.pack("%di" % len(koffsets), *koffsets)
    data += struct.pack("%di" % len(voffsets), *voffsets)
    data += ids + strs
    tmp = path + ".tmp"
    with open(tmp, "wb") as fp:
        fp.write(data)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Sprachen
# --------------------------------------------------------------------------

def available_languages():
    """['en', ...] - Englisch ist immer da, weitere aus locale/*/LC_MESSAGES/wfmix.po"""
    langs = ["en"]
    if os.path.isdir(LOCALE_DIR):
        for d in sorted(os.listdir(LOCALE_DIR)):
            if os.path.isfile(os.path.join(LOCALE_DIR, d, "LC_MESSAGES", DOMAIN + ".po")) and d != "en":
                langs.append(d)
    return langs


def _load(lang):
    with _lock:
        if lang in _translations:
            return _translations[lang]
        t = gettext.NullTranslations()
        po = os.path.join(LOCALE_DIR, lang, "LC_MESSAGES", DOMAIN + ".po")
        if lang != "en" and os.path.isfile(po):
            mo_dir = os.path.join(CACHE_DIR, lang, "LC_MESSAGES")
            mo = os.path.join(mo_dir, DOMAIN + ".mo")
            try:
                if not os.path.isfile(mo) or os.path.getmtime(mo) < os.path.getmtime(po):
                    os.makedirs(mo_dir, exist_ok=True)
                    write_mo(parse_po(po), mo)
                t = gettext.translation(DOMAIN, CACHE_DIR, languages=[lang])
            except (OSError, ValueError, SyntaxError):
                t = gettext.NullTranslations()
        _translations[lang] = t
        return t


def set_language(lang, default=True):
    """Sprache setzen (default=True: fuer alle Threads, sonst nur diesen)."""
    if lang not in available_languages():
        lang = "en"
    if default:
        _default["lang"] = lang
    else:
        _current.lang = lang
    _load(lang)
    return lang


def get_language():
    return getattr(_current, "lang", None) or _default["lang"]


def _(message):
    """Uebersetzt message (englischer Quelltext) in die aktuelle Sprache."""
    return _load(get_language()).gettext(message)


def N_(message):
    """Markiert Text zum Uebersetzen, ohne ihn sofort zu uebersetzen."""
    return message


def catalog(lang):
    """Alle Uebersetzungen einer Sprache als dict (fuer die Weboberflaeche)."""
    if lang == "en" or lang not in available_languages():
        return {}
    po = os.path.join(LOCALE_DIR, lang, "LC_MESSAGES", DOMAIN + ".po")
    try:
        entries = parse_po(po)
    except (OSError, ValueError, SyntaxError):
        return {}
    entries.pop("", None)
    return entries


def language_name(lang):
    names = {"en": "EN", "de": "DE"}
    return names.get(lang, lang.upper())


def default_language(environ=None):
    environ = os.environ if environ is None else environ
    langs = available_languages()
    for k in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        v = (environ.get(k) or "").split(":")[0]
        if v:
            code = v.split(".")[0].split("_")[0].lower()
            return code if code in langs else "en"
    return "en"
