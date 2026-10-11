#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_po.py - extract translatable texts and update the translation files.

    python3 tools/update_po.py            # update locale/wfmix.pot and all .po files
    python3 tools/update_po.py --init fr  # start a new language (locale/fr/LC_MESSAGES/wfmix.po)
    python3 tools/update_po.py --check    # exit 1 if a .po file misses translations

Sources:
- Python files: string literals in _("...") and N_("...")
- wfmix.html:   T("...") calls and data-t / data-th / data-tt / data-tp attributes

Merging keeps existing translations, adds new texts with an empty msgstr and
drops texts that no longer exist. The result is a normal gettext .po file that
can be edited with any PO editor (e.g. Poedit) or checked with `msgfmt -c`.
"""

import argparse
import ast
import datetime
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from wfmix_i18n import DOMAIN, LOCALE_DIR, parse_po  # noqa: E402

PY_FILES = ["wfmix.py", "wfmix_core.py"]
HTML_FILES = ["wfmix.html"]


def extract():
    """Liefert geordnetes dict msgid -> {"refs": [...], "py": bool}."""
    msgs = {}

    def add(msgid, ref, py):
        e = msgs.setdefault(msgid, {"refs": [], "py": False})
        e["refs"].append(ref)
        e["py"] = e["py"] or py

    for fn in PY_FILES:
        with open(os.path.join(ROOT, fn), encoding="utf-8") as fp:
            tree = ast.parse(fp.read(), fn)
        nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id in ("_", "N_") and n.args and isinstance(n.args[0], ast.Constant)
                 and isinstance(n.args[0].value, str)]
        for n in sorted(nodes, key=lambda n: (n.lineno, n.col_offset)):
            add(n.args[0].value, "%s:%d" % (fn, n.lineno), True)
    for fn in HTML_FILES:
        with open(os.path.join(ROOT, fn), encoding="utf-8") as fp:
            text = fp.read()
        found = []
        for mo in re.finditer(r'\bT\(("(?:[^"\\]|\\.)*")', text):
            found.append((mo.start(), json.loads(mo.group(1))))
        for mo in re.finditer(r'\bdata-t[htp]?="([^"]*)"', text):
            found.append((mo.start(), html.unescape(mo.group(1))))
        for pos, msgid in sorted(found):
            add(msgid, "%s:%d" % (fn, text.count("\n", 0, pos) + 1), False)
    return msgs


def po_str(s):
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    if "\n" in s and len(s) > 1:
        parts = s.split("\n")
        lines = ['""'] + ['"%s\\n"' % p for p in parts[:-1]] + (['"%s"' % parts[-1]] if parts[-1] else [])
        return "\n".join(lines)
    return '"%s"' % s.replace("\n", "\\n")


def header(lang):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M+0000")
    lines = [
        "Project-Id-Version: wfmix",
        "POT-Creation-Date: %s" % now,
        "PO-Revision-Date: %s" % now,
        "Last-Translator: wfmix contributors",
        "Language-Team: %s" % (lang or "none"),
        "Language: %s" % (lang or ""),
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "Content-Transfer-Encoding: 8bit",
        "Plural-Forms: nplurals=2; plural=(n != 1);",
    ]
    return "\n".join(lines) + "\n"


def write_po(path, msgs, translations, lang):
    out = ["# wfmix translations%s" % ((" – " + lang) if lang else " – template"),
           "# This file is distributed under the same license as wfmix (MIT).",
           "#",
           'msgid ""',
           "msgstr " + po_str(header(lang)), ""]
    for msgid, e in msgs.items():
        out.append("#: " + " ".join(e["refs"]))
        if e["py"] and re.search(r"%(\(\w+\))?[sdrf]", msgid):
            out.append("#, python-format")
        out.append("msgid " + po_str(msgid))
        out.append("msgstr " + po_str(translations.get(msgid, "")))
        out.append("")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out))


def po_path(lang):
    return os.path.join(LOCALE_DIR, lang, "LC_MESSAGES", DOMAIN + ".po")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", metavar="LANG", help="create a new language file")
    ap.add_argument("--check", action="store_true", help="report missing translations, exit 1 if any")
    a = ap.parse_args()

    msgs = extract()
    write_po(os.path.join(LOCALE_DIR, DOMAIN + ".pot"), msgs, {}, "")
    print("%d texts -> locale/%s.pot" % (len(msgs), DOMAIN))

    if a.init:
        if not re.match(r"^[a-z]{2,3}(_[A-Z]{2})?$", a.init):
            sys.exit("language code like 'fr' or 'pt_BR' expected")
        if os.path.exists(po_path(a.init)):
            sys.exit("%s already exists" % po_path(a.init))
        write_po(po_path(a.init), msgs, {}, a.init)
        print("created", os.path.relpath(po_path(a.init), ROOT))

    missing_total = 0
    if os.path.isdir(LOCALE_DIR):
        for lang in sorted(os.listdir(LOCALE_DIR)):
            p = po_path(lang)
            if not os.path.isfile(p):
                continue
            old = parse_po(p)
            old.pop("", None)
            missing = [m for m in msgs if not old.get(m)]
            dropped = [m for m in old if m not in msgs]
            if not a.check:
                write_po(p, msgs, old, lang)
            missing_total += len(missing)
            print("%s: %d translated, %d missing, %d obsolete removed"
                  % (lang, len(msgs) - len(missing), len(missing), 0 if a.check else len(dropped)))
            if a.check:
                for m in missing:
                    print("  missing: %r" % m)
    if a.check and missing_total:
        sys.exit(1)


if __name__ == "__main__":
    main()
