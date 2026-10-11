# -*- coding: utf-8 -*-
"""
wfmix_core - QML-Zerlegung und Kombinier-Logik fuer AsteroidOS-Watchfaces.

Zerlegt Watchface-QML-Dateien in Bausteine (die direkten Kinder des
Wurzel-Objekts), loest Abhaengigkeiten (ids, Wurzel-Properties, Funktionen,
Handler) auf und baut aus einer Auswahl von Bausteinen mehrerer Watchfaces
ein neues, lauffaehiges Watchface inkl. Bildern, Schriften und Vorschau.

Nur Python-Standardbibliothek.
"""

import json
import os
import re
import shutil

WF_SUBDIR = os.path.join("usr", "share", "asteroid-launcher", "watchfaces")

# --------------------------------------------------------------------------
# Sprache / language ("de" oder "en") - wird von wfmix.py gesetzt
# --------------------------------------------------------------------------
LANG = "de"


def tr(de, en):
    """Liefert den Text in der eingestellten Sprache."""
    return en if LANG == "en" else de


# --------------------------------------------------------------------------
# Qt5 <-> Qt6
# --------------------------------------------------------------------------
# Kanonische Modulnamen (Qt6) und Standardversionen fuer Qt5
QT5_NAME = {"Qt5Compat.GraphicalEffects": "QtGraphicalEffects"}
QT6_NAME = {"QtGraphicalEffects": "Qt5Compat.GraphicalEffects"}
QT5_VERSION = {
    "QtQuick": "2.15", "QtQuick.Shapes": "1.15", "QtQuick.Controls": "2.15",
    "QtQuick.Layouts": "1.15", "QtQuick.Window": "2.15", "QtQuick.Particles": "2.15",
    "QtQml": "2.15", "QtQml.Models": "2.15", "QtGraphicalEffects": "1.15",
    "Nemo.Mce": "1.0", "Nemo.Configuration": "1.0", "Nemo.DBus": "2.0",
    "Nemo.Time": "1.0", "org.asteroid.controls": "1.0", "org.asteroid.utils": "1.0",
    "org.asteroid.sensorlogd": "1.0", "QtSensors": "5.11", "Connman": "0.2",
    "QtPositioning": "5.15", "Qt.labs.settings": "1.0", "QtMultimedia": "5.15",
}
QT6_ONLY_MODULES = {"QtQuick.Effects", "QtCore"}


def qt_only(text):
    """'6' wenn der Code nur unter Qt6 laeuft, '5' wenn nur unter Qt5, sonst ''."""
    m = mask(text)
    if re.search(r"(?<![\w.])(MultiEffect|FrameAnimation)\s*\{", m) or ".qsb" in text:
        return "6"
    if re.search(r"(?<![\w.])ShaderEffect\s*\{", m) and re.search(r"(gl_FragColor|varying|uniform\s)", text):
        return "5"
    return ""


# Typen, die nichts zeichnen, sondern nur Daten liefern oder andere Bausteine
# steuern. Solche Bausteine werden automatisch mitgenommen, wenn sie gewaehlte
# Bausteine ansteuern (z.B. "Connections { target: wallClock ... }").
HELPER_TYPES = {
    "Connections", "Timer", "Binding", "QtObject", "Settings",
    "ConfigurationValue", "NetworkTechnology", "Compass", "HrmSensor",
    "StepsDataLoader", "WeatherModel", "ListModel", "PropertyAnimation",
    "NumberAnimation", "SequentialAnimation", "ParallelAnimation",
    "ColorAnimation", "SensorLogd",
}


def is_helper_type(t):
    base = t.split(".")[-1]
    return (base in HELPER_TYPES or base.startswith("Mce")
            or base.endswith("Sensor") or base.endswith("Model")
            or base.endswith("Animation"))


# --------------------------------------------------------------------------
# Lexer: erzeugt eine "maskierte" Kopie des Textes gleicher Laenge, in der
# Kommentare, Strings und Regex-Literale durch Leerzeichen ersetzt sind
# (Anfuehrungszeichen bleiben stehen). Strukturanalyse und Bezeichnersuche
# laufen auf der Maske, Ausschnitte werden aus dem Original genommen.
# --------------------------------------------------------------------------

_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")


def mask(text):
    out = list(text)
    i, n = 0, len(text)
    last_sig = ""  # letztes signifikantes Zeichen (fuer Regex-Erkennung)
    last_word = ""
    while i < n:
        c = text[i]
        nx = text[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and nx == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if text[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if c in "\"'`":
            q = c
            j = i + 1
            while j < n and text[j] != q:
                if text[j] == "\\":
                    out[j] = " "
                    j += 1
                    if j < n:
                        out[j] = " "
                    j += 1
                    continue
                if text[j] == "\n" and q != "`":
                    break
                out[j] = " "
                j += 1
            i = j + 1
            last_sig = q
            last_word = ""
            continue
        if c == "/" and (last_sig in _REGEX_PREV or last_sig == ""
                         or last_word in ("return", "typeof", "case")):
            # Regex-Literal
            j = i + 1
            in_cls = False
            ok = False
            while j < n and text[j] != "\n":
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_cls = True
                elif ch == "]":
                    in_cls = False
                elif ch == "/" and not in_cls:
                    ok = True
                    break
                j += 1
            if ok:
                for k in range(i + 1, j):
                    out[k] = " "
                j += 1
                while j < n and text[j].isalpha():
                    j += 1
                i = j
                last_sig = "/"
                last_word = ""
                continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            last_word = text[i:j]
            last_sig = "a"
            i = j
            continue
        if not c.isspace():
            last_sig = c
            last_word = ""
        i += 1
    return "".join(out)


_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {")", "]", "}"}


def match_brace(m, pos):
    """m[pos] ist eine oeffnende Klammer; liefert Index der passenden."""
    depth = 0
    for i in range(pos, len(m)):
        ch = m[i]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
            if depth == 0:
                return i
    return len(m) - 1


_CONT_END = re.compile(r"(?:[-+*/%=<>!&|^?:,.(\[]|\belse|\bdo)\s*$")
_INCDEC_END = re.compile(r"(\+\+|--)\s*$")
_CONT_START = re.compile(r"^\s*(?:\?|:(?!:)|\.(?!\.)|&&|\|\||\+|\*|/|%|else\b|catch\b|finally\b|\)|\])")
_CTRL_START = re.compile(r"^\s*(?:if|for|while|with)\b")


def split_members(m, start, end):
    """Zerlegt den Bereich m[start:end] (Rumpf eines Objekts oder einer
    JS-Funktion) in Mitglieder/Anweisungen. Liefert Liste von (s, e)."""
    spans = []
    depth = 0
    cur = None
    i = start
    while i < end:
        ch = m[i]
        if cur is None:
            if ch.isspace() or ch == ";":
                i += 1
                continue
            cur = i
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif depth == 0 and ch == ";":
            spans.append((cur, i + 1))
            cur = None
            i += 1
            continue
        elif depth == 0 and ch == "\n":
            seg = m[cur:i]
            rest = m[i + 1:end]
            nl = rest.lstrip()
            cont = False
            if _CONT_END.search(seg) and not _INCDEC_END.search(seg):
                cont = True
            elif nl and _CONT_START.match(nl):
                cont = True
            elif _CTRL_START.match(seg) and seg.rstrip().endswith(")"):
                cont = True
            if not cont:
                spans.append((cur, i))
                cur = None
        i += 1
    if cur is not None and m[cur:end].strip():
        spans.append((cur, end))
    # Ende zurechtstutzen (kein Leerraum am Ende)
    res = []
    for s, e in spans:
        while e > s and m[e - 1].isspace():
            e -= 1
        res.append((s, e))
    return res


_IDENT = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)")
_ID_DECL = re.compile(r"(?<![\w$.])id\s*:\s*([A-Za-z_]\w*)")
_PROP = re.compile(r"^(?:(?:readonly|default|required)\s+)*property\s+[\w.<>]+\s+([A-Za-z_]\w*)")
_ALIASP = re.compile(r"^(?:(?:readonly|default|required)\s+)*property\s+alias\s+([A-Za-z_]\w*)")
_SIGNAL = re.compile(r"^signal\s+([A-Za-z_]\w*)")
_FUNC = re.compile(r"^function\s+([A-Za-z_]\w*)")
_IDM = re.compile(r"^id\s*:\s*([A-Za-z_]\w*)\s*;?$")
_OBJ = re.compile(r"^([A-Z][\w]*(?:\.[A-Z][\w]*)*)(?:\s+on\s+[\w.]+)?\s*\{")
_GROUP = re.compile(r"^([a-z][\w.]*)\s*\{")
_ASSIGN = re.compile(r"^([A-Za-z_][\w.]*)\s*:")
_ENUM = re.compile(r"^enum\s+")
_COMP = re.compile(r"^component\s+([A-Z]\w*)\s*:")


class Member(object):
    __slots__ = ("kind", "name", "s", "e", "text", "mtext", "lead", "indent")

    def __init__(self, kind, name, s, e, text, mtext, lead, indent):
        self.kind, self.name = kind, name
        self.s, self.e = s, e
        self.text, self.mtext = text, mtext
        self.lead = lead      # vorangestellte Kommentarzeilen (Original)
        self.indent = indent  # Einrueckung der ersten Zeile


def _line_indent(text, pos):
    ls = text.rfind("\n", 0, pos) + 1
    return text[ls:pos] if text[ls:pos].strip() == "" else ""


def parse_members(text, m, start, end):
    members = []
    prev_end = start
    for s, e in split_members(m, start, end):
        t, mt = text[s:e], m[s:e]
        between = text[prev_end:s]
        lead = ""
        if between.strip():
            # Kommentare zwischen den Mitgliedern -> diesem Mitglied voranstellen
            lines = [l.strip() for l in between.split("\n")]
            lead = "\n".join(l for l in lines if l)
        prev_end = e
        ms = mt.strip()
        kind, name = "other", ""
        mm = _ALIASP.match(ms) or _PROP.match(ms)
        if mm:
            kind, name = "property", mm.group(1)
        elif _SIGNAL.match(ms):
            kind, name = "signal", _SIGNAL.match(ms).group(1)
        elif _FUNC.match(ms):
            kind, name = "function", _FUNC.match(ms).group(1)
        elif _IDM.match(ms):
            kind, name = "id", _IDM.match(ms).group(1)
        elif _COMP.match(ms):
            kind, name = "component", _COMP.match(ms).group(1)
        elif _OBJ.match(ms):
            kind, name = "object", _OBJ.match(ms).group(1)
        elif _GROUP.match(ms):
            kind, name = "group", _GROUP.match(ms).group(1)
        elif _ASSIGN.match(ms):
            name = _ASSIGN.match(ms).group(1)
            last = name.split(".")[-1]
            if re.match(r"^on[A-Z]", last):
                kind = "handler"
            else:
                kind = "assign"
        elif _ENUM.match(ms):
            kind = "enum"
        members.append(Member(kind, name, s, e, t, mt, lead,
                              _line_indent(text, s)))
    return members


def idents(mt):
    return set(_IDENT.findall(mt))


def ids_declared(mt):
    return set(_ID_DECL.findall(mt))


def root_refs(mt, root_id):
    if not root_id:
        return set()
    return set(re.findall(r"(?<![\w$.])" + re.escape(root_id) + r"\s*\.\s*([A-Za-z_]\w*)", mt))


def rename_idents(text, mapping, root_id=None, prop_map=None, local_props=()):
    """Benennt Bezeichner im Code um (nicht in Strings/Kommentaren).
    mapping: einfache Bezeichner (nicht nach '.') -> neuer Name
    prop_map: Wurzel-Properties -> neuer Name; ersetzt 'root.x' und blankes
              'x' (sofern nicht lokal deklariert)."""
    if not mapping and not prop_map:
        return text
    m = mask(text)
    reps = []
    full = dict(mapping or {})
    if prop_map:
        for k, v in prop_map.items():
            if k not in local_props and k not in full:
                full[k] = v
    for mo in _IDENT.finditer(m):
        w = mo.group(1)
        if w in full:
            # Property-Namen auf der linken Seite eines Doppelpunkts nicht
            # anfassen (z.B. "width: ..."), ausser es ist eine id-Zuweisung.
            after = m[mo.end():mo.end() + 3].lstrip()
            before = m[:mo.start()].rstrip()
            if after.startswith(":") and not after.startswith("::") \
                    and not before.endswith("?") and not before.endswith("id") \
                    and not re.search(r"\bid\s*:\s*$", m[max(0, mo.start() - 20):mo.start()]):
                # "name:" am Zeilenanfang = Property-Zuweisung -> nicht umbenennen
                ls = m.rfind("\n", 0, mo.start()) + 1
                if m[ls:mo.start()].strip() == "":
                    continue
            reps.append((mo.start(), mo.end(), full[w]))
    if prop_map and root_id:
        pat = re.compile(r"(?<![\w$.])" + re.escape(root_id) + r"\s*\.\s*([A-Za-z_]\w*)")
        for mo in pat.finditer(m):
            if mo.group(1) in prop_map:
                reps.append((mo.start(1), mo.end(1), prop_map[mo.group(1)]))
    # Handler-Namen fuer umbenannte Properties (onFooChanged)
    if prop_map:
        for k, v in prop_map.items():
            pat = re.compile(r"(?<![\w$.])on" + re.escape(k[0].upper() + k[1:]) + r"Changed\b")
            for mo in pat.finditer(m):
                reps.append((mo.start(), mo.end(), "on" + v[0].upper() + v[1:] + "Changed"))
    reps.sort()
    out, last = [], 0
    for s, e, r in reps:
        if s < last:
            continue
        out.append(text[last:s])
        out.append(r)
        last = e
    out.append(text[last:])
    return "".join(out)


def replace_root_id(text, old, new):
    if not old or old == new:
        return text
    m = mask(text)
    out, last = [], 0
    for mo in re.finditer(r"(?<![\w$.])" + re.escape(old) + r"(?![\w$])", m):
        # "id: old" selbst wird nicht in Bausteinen vorkommen
        out.append(text[last:mo.start()])
        out.append(new)
        last = mo.end()
    out.append(text[last:])
    return "".join(out)


# --------------------------------------------------------------------------
# Watchface-Analyse
# --------------------------------------------------------------------------

def _humanize(s):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = s.replace("_", " ").strip()
    return s[:1].upper() + s[1:]


class Block(object):
    def __init__(self, face, member, key, index):
        self.face = face
        self.member = member
        self.key = key
        self.index = index
        self.type = member.name
        self.text = member.text
        self.mtext = member.mtext
        self.lead = member.lead
        body_m = member.mtext
        ob = body_m.find("{")
        sub = parse_members(member.text, member.mtext, ob + 1, len(body_m) - 1) if ob >= 0 else []
        self.own_id = ""
        for sm in sub:
            if sm.kind == "id":
                self.own_id = sm.name
        self.ids = ids_declared(body_m)
        self.refs = idents(body_m)
        self.root_refs = root_refs(body_m, face.root_id)
        self.helper = is_helper_type(self.type)
        self.only = qt_only(self.text)
        self.lines = self.text.count("\n") + 1
        self.local_props = set()
        for mo in re.finditer(r"property\s+[\w.<>]+\s+([A-Za-z_]\w*)", body_m):
            self.local_props.add(mo.group(1))

    @property
    def label(self):
        hint = ""
        if self.lead:
            c = self.lead.splitlines()[-1].lstrip("/* ").rstrip("*/ ")
            if c:
                hint = c
        if self.own_id:
            name = _humanize(self.own_id)
        else:
            name = self.type
            m = re.search(r'source\s*:\s*"[^"]*?([\w-]+)\.(?:svg|png|jpg)"', self.text)
            if m:
                name += " (" + m.group(1) + ")"
            else:
                m = re.search(r'\btext\s*:\s*"([^"]{1,24})"', self.text)
                if m:
                    name += ' "' + m.group(1) + '"'
        return name + ((" – " + hint) if hint and hint.lower() not in name.lower() else "")

    def to_json(self, with_code=True):
        d = {
            "key": self.key, "type": self.type, "id": self.own_id,
            "label": self.label, "lines": self.lines, "helper": self.helper,
            "only": self.only,
            "ids": sorted(self.ids),
            "deps": sorted(self.face.block_deps(self)),
            "uses": sorted(self.face.block_root_uses(self)),
        }
        if with_code:
            d["code"] = (self.lead + "\n" if self.lead else "") + self.text
        return d


class Face(object):
    """Eine Watchface-QML-Datei."""

    def __init__(self, face_dir, qml_path):
        self.dir = face_dir                      # Ordner im Repo (z.B. .../arc)
        self.qml_path = qml_path
        self.qml_name = os.path.splitext(os.path.basename(qml_path))[0]
        self.folder = os.path.basename(face_dir)
        self.fid = self.qml_name if self.qml_name == self.folder else self.folder + "/" + self.qml_name
        with open(qml_path, encoding="utf-8", errors="replace") as f:
            self.text = f.read().replace("\r\n", "\n").replace("\t", "    ")
        self.m = mask(self.text)
        self._parse()

    def _parse(self):
        text, m = self.text, self.m
        mo = re.search(r"^[ \t]*([A-Z][\w]*(?:\.[A-Z][\w]*)*)\s*\{", m, re.M)
        if not mo:
            raise ValueError(tr("kein Wurzel-Objekt gefunden", "no root object found"))
        self.root_type = mo.group(1)
        ob = m.index("{", mo.start())
        cb = match_brace(m, ob)
        pre = text[:mo.start()]
        # Kopf: Kommentare/Lizenz, Importe, Pragmas
        self.imports = []
        self.pragmas = []
        pm = m[:mo.start()]
        for line_t, line_m in zip(pre.split("\n"), pm.split("\n")):
            ls = line_m.strip()
            if ls.startswith("import "):
                self.imports.append(line_t.strip().rstrip(";"))
            elif ls.startswith("pragma "):
                self.pragmas.append(line_t.strip())
        first_imp = len(pre)
        mi = re.search(r"^[ \t]*(import|pragma)\s", pm, re.M)
        if mi:
            first_imp = mi.start()
        self.header = pre[:first_imp].strip()
        # Qt-Version der Datei: unversionierte Importe / Qt5Compat => Qt6
        self.qt = "5"
        for imp in self.imports:
            mod, ver = _import_key(imp)
            if mod.startswith('"'):
                continue
            if not ver or mod.split("|")[0] in ("Qt5Compat.GraphicalEffects",) or mod.split("|")[0] in QT6_ONLY_MODULES:
                self.qt = "6"
                break
        self.members = parse_members(text, m, ob + 1, cb)
        self.root_id = ""
        self.props = {}      # name -> Member
        self.funcs = {}
        self.signals = {}
        self.handlers = []   # Member
        self.assigns = []    # Member (inkl. groups)
        self.blocks = []
        counts = {}
        for mem in self.members:
            if mem.kind == "id":
                self.root_id = mem.name
            elif mem.kind == "property":
                self.props[mem.name] = mem
            elif mem.kind == "function":
                self.funcs[mem.name] = mem
            elif mem.kind == "signal":
                self.signals[mem.name] = mem
            elif mem.kind == "component":
                self.signals[mem.name] = mem   # wie Signal: bei Bedarf mitnehmen
            elif mem.kind == "handler":
                self.handlers.append(mem)
            elif mem.kind in ("assign", "group", "enum", "other"):
                self.assigns.append(mem)
        for mem in self.members:
            if mem.kind == "object":
                b = Block(self, mem, "", len(self.blocks))
                if b.own_id:
                    key = b.own_id
                else:
                    counts[mem.name] = counts.get(mem.name, 0) + 1
                    key = "%s#%d" % (mem.name, counts[mem.name])
                b.key = key
                self.blocks.append(b)
        self.block_by_key = {b.key: b for b in self.blocks}
        self.id_owner = {}
        for b in self.blocks:
            for i in b.ids:
                self.id_owner[i] = b
        self.all_ids = set(self.id_owner)
        if self.root_id:
            self.all_ids.add(self.root_id)
        self.rootish = set(self.props) | set(self.funcs) | set(self.signals)

    # -- Abhaengigkeiten ---------------------------------------------------
    def block_deps(self, b):
        """Andere Bausteine dieses Watchfaces, deren ids b benutzt."""
        res = set()
        for r in b.refs:
            o = self.id_owner.get(r)
            if o is not None and o is not b:
                res.add(o.key)
        return res

    def block_root_uses(self, b):
        res = set(b.root_refs) & self.rootish
        res |= (b.refs - b.local_props) & self.rootish
        return res

    def to_json(self, with_blocks=False):
        d = {
            "id": self.fid, "folder": self.folder, "qml": self.qml_name,
            "rootType": self.root_type, "rootId": self.root_id,
            "blocks": len(self.blocks), "qt": self.qt,
        }
        if with_blocks:
            d["blockList"] = [b.to_json() for b in self.blocks]
            d["props"] = [{"name": k, "code": v.text} for k, v in self.props.items()]
            d["functions"] = sorted(self.funcs)
            d["handlers"] = [{"name": h.name, "code": h.text} for h in self.handlers]
            d["imports"] = self.imports
        return d


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------

def component_files(wfd):
    """QML-Dateien im watchfaces-Ordner, die von anderen als Typ benutzt
    werden (z.B. Arc.qml) - das sind keine eigenstaendigen Watchfaces."""
    try:
        files = [f for f in os.listdir(wfd) if f.endswith(".qml") and not f.startswith(".")]
    except OSError:
        return set()
    texts = {}
    for f in files:
        try:
            with open(os.path.join(wfd, f), encoding="utf-8", errors="replace") as fp:
                texts[f] = mask(fp.read())
        except OSError:
            texts[f] = ""
    comps = set()
    for f in files:
        n = f[:-4]
        if not n[:1].isupper():
            continue
        pat = re.compile(r"(?<![\w.])" + re.escape(n) + r"\s*\{")
        if any(pat.search(t) for g, t in texts.items() if g != f):
            comps.add(f)
    return comps


def detect_repo_qt(path):
    """Qt-Version des Repo-Stands anhand von loader.qml ("5" oder "6")."""
    try:
        with open(os.path.join(path, "loader.qml"), encoding="utf-8", errors="replace") as fp:
            return "6" if "WatchfaceConfig" in fp.read() else "5"
    except OSError:
        return "5"


class Repo(object):
    def __init__(self, path, workdir=None):
        self.path = os.path.abspath(path)
        self.workdir = os.path.abspath(workdir or os.path.join(self.path, "mixed"))
        self._cache = {}
        self.errors = {}
        self.qt = detect_repo_qt(self.path)

    def face_dirs(self):
        res = []
        for base in (self.path, self.workdir):
            if not os.path.isdir(base):
                continue
            for n in sorted(os.listdir(base)):
                d = os.path.join(base, n)
                if os.path.isdir(os.path.join(d, WF_SUBDIR)):
                    res.append((d, base == self.workdir))
        return res

    def faces(self):
        res = []
        for d, own in self.face_dirs():
            wfd = os.path.join(d, WF_SUBDIR)
            comps = component_files(wfd)
            try:
                names = sorted(os.listdir(wfd))
            except OSError:
                continue
            for fn in names:
                if not fn.endswith(".qml") or fn in comps or fn.startswith("."):
                    continue
                p = os.path.join(wfd, fn)
                f = self.load(p, d)
                if f is not None:
                    f.own = own
                    res.append(f)
        return res

    def load(self, qml_path, face_dir):
        try:
            st = os.stat(qml_path).st_mtime
        except OSError:
            return None
        c = self._cache.get(qml_path)
        if c and c[0] == st:
            return c[1]
        try:
            f = Face(face_dir, qml_path)
        except Exception as e:  # noqa
            self.errors[qml_path] = str(e)
            return None
        self._cache[qml_path] = (st, f)
        return f

    def face(self, fid):
        for f in self.faces():
            if f.fid == fid:
                return f
        return None


# --------------------------------------------------------------------------
# Zusammenbau
# --------------------------------------------------------------------------

def _filter_statements(code, missing, log, where):
    """Entfernt aus einem JS-Block '{ ... }' alle Anweisungen, die ids aus
    'missing' benutzen. Liefert neuen Code oder None, wenn nichts uebrig."""
    m = mask(code)
    ob = m.find("{")
    if ob < 0:
        refs = idents(m)
        if refs & missing:
            log.append(tr("  – entfernt in %s: %s", "  – removed in %s: %s") % (where, " ".join(code.split())[:90]))
            return None
        return code
    cb = match_brace(m, ob)
    stm = split_members(m, ob + 1, cb)
    keep = []
    missing = set(missing)
    for s, e in stm:
        if idents(m[s:e]) & missing:
            log.append(tr("  – entfernt in %s: %s", "  – removed in %s: %s") % (where, " ".join(code[s:e].split())[:90]))
            # Variablen, die hier deklariert wurden, sind jetzt auch "fehlend"
            missing |= set(re.findall(r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)", m[s:e]))
            continue
        keep.append((s, e))
    if not keep:
        return None
    if len(keep) == len(stm):
        return code
    ind = _line_indent(code, stm[0][0]) if stm else "    "
    ind = ind or "    "
    body = "\n".join(ind + code[s:e] for s, e in keep)
    close_ind = _line_indent(code, cb)
    return code[:ob + 1] + "\n" + body + "\n" + close_ind + code[cb:]


def _filter_object(text, missing, log, where, depth=0):
    """Filtert ein Objekt (Helfer-Baustein) rekursiv: Bindungen und
    Anweisungen mit fehlenden ids fliegen raus. None = ganz verwerfen."""
    m = mask(text)
    ob = m.find("{")
    if ob < 0:
        return text
    cb = match_brace(m, ob)
    mems = parse_members(text, m, ob + 1, cb)
    out = []
    changed = False
    for mem in mems:
        refs = idents(mem.mtext)
        if not (refs & missing):
            out.append(mem)
            continue
        changed = True
        if mem.kind in ("handler", "function"):
            # Funktionsrumpf bzw. Handler-Rumpf filtern
            if mem.kind == "handler":
                colon = mem.mtext.index(":")
                val = mem.text[colon + 1:].strip()
                nv = _filter_statements(val, missing, log, where)
                if nv is None:
                    continue
                mem.text = mem.text[:colon + 1] + " " + nv
            else:
                nv = _filter_statements(mem.text, missing, log, where)
                if nv is None:
                    continue
                mem.text = nv
            out.append(mem)
        elif mem.kind == "object":
            nv = _filter_object(mem.text, missing, log, where, depth + 1)
            if nv is not None:
                mem.text = nv
                out.append(mem)
        else:
            if mem.name == "target":
                log.append(tr("  – %s verworfen (Ziel fehlt)", "  – %s dropped (target missing)") % where)
                return None
            log.append(tr("  – entfernt in %s: %s", "  – removed in %s: %s") % (where, " ".join(mem.text.split())[:90]))
    if not changed:
        return text
    real = [x for x in out if x.kind not in ("id",)]
    if not real or all(x.kind in ("assign", "group", "id") for x in out):
        if depth == 0:
            log.append(tr("  – %s verworfen (nichts mehr zu tun)", "  – %s dropped (nothing left to do)") % where)
            return None
    parts = []
    for mem in out:
        lead = ""
        if mem.lead:
            lead = "\n".join(mem.indent + l for l in mem.lead.split("\n")) + "\n"
        parts.append(lead + mem.indent + mem.text)
    close_ind = _line_indent(text, cb)
    return text[:ob + 1] + "\n" + "\n\n".join(parts) + "\n" + close_ind + "}"


def _import_key(imp):
    mo = re.match(r'import\s+("[^"]+"|[\w.]+)(?:\s+([\d.]+))?(?:\s+as\s+(\w+))?', imp)
    if not mo:
        return imp, ()
    mod = mo.group(1) + ("|" + mo.group(3) if mo.group(3) else "")
    ver = tuple(int(x) for x in (mo.group(2) or "").split(".") if x.isdigit())
    return mod, ver


def emit_imports(lists, target, log=None):
    """Fuehrt die Importe aller Quellen zusammen und schreibt sie passend
    fuer Qt5 (mit Versionsnummern) oder Qt6 (ohne) um."""
    best = {}      # kanonischer Modulname(|alias) -> (version tuple, alias, quoted line)
    order = []
    for lst in lists:
        for imp in lst:
            mo = re.match(r'import\s+("[^"]+"|[\w.]+)(?:\s+([\d.]+))?(?:\s+as\s+(\w+))?', imp)
            if not mo:
                continue
            mod, ver, alias = mo.group(1), mo.group(2) or "", mo.group(3) or ""
            if not mod.startswith('"'):
                mod = QT6_NAME.get(mod, mod)
            key = mod + "|" + alias
            vt = tuple(int(x) for x in ver.split(".") if x.isdigit())
            if key not in best:
                order.append(key)
                best[key] = (vt, alias, mod)
            elif vt > best[key][0]:
                best[key] = (vt, alias, mod)
    if True:  # Qt5 und Qt6: ShapePath.RoundCap & Co. brauchen den unqualifizierten Import
        for key in list(order):
            vt, alias, mod = best[key]
            plain = mod + "|"
            if alias and mod == "QtQuick.Shapes" and plain not in best:
                # Qt5 kennt ShapePath.RoundCap & Co. nur ueber den unqualifizierten Import
                best[plain] = (vt, "", mod)
                order.append(plain)
    out = []
    for key in order:
        vt, alias, mod = best[key]
        suffix = (" as " + alias) if alias else ""
        if mod.startswith('"'):
            out.append("import %s%s" % (mod, suffix))
            continue
        if target == "5":
            if mod in QT6_ONLY_MODULES:
                if log is not None:
                    log.append(tr("! Import '%s' gibt es unter Qt5 nicht – entfernt",
                                  "! Import '%s' does not exist on Qt5 – removed") % mod)
                continue
            name = QT5_NAME.get(mod, mod)
            ver = ".".join(str(x) for x in vt) if vt else QT5_VERSION.get(name, "1.0")
            if name == "QtGraphicalEffects" and not vt:
                ver = "1.15"
            out.append("import %s %s%s" % (name, ver, suffix))
        else:
            out.append("import %s%s" % (mod, suffix))
    # QtGraphicalEffects zuletzt, damit dessen LinearGradient & Co. Vorrang vor
    # gleichnamigen Typen aus QtQuick.Shapes haben (spaetere Importe gewinnen).
    return sorted(out, key=lambda s: (s.startswith('import "'), "GraphicalEffects" in s, s))


def _prop_val(body, name):
    mo = re.search(r"^\s*%s\s*:\s*(.+?)\s*;?\s*$" % re.escape(name), body, re.M)
    return mo.group(1) if mo else None


def adapt_effects(text, target, log, where):
    """Grafik-Effekte, die es nur in einer Qt-Version gibt, anpassen:
    Qt5-Ziel: MultiEffect-Schatten -> DropShadow, andere MultiEffect und
              Qt6-Shader (.qsb) werden entfernt.
    Qt6-Ziel: Qt5-Shader (GLSL im Text) werden entfernt.
    Liefert (neuer Text, brauchtGraphicalEffects)."""
    need_ge = False
    m = mask(text)
    hits = list(re.finditer(r"layer\.effect\s*:\s*(MultiEffect|ShaderEffect)\s*\{", m))
    for mo in reversed(hits):
        m = mask(text)
        ob = m.index("{", mo.start())
        cb = match_brace(m, ob)
        body = text[ob + 1:cb]
        kind = mo.group(1)
        ls = text.rfind("\n", 0, mo.start()) + 1
        indent = text[ls:mo.start()]
        end = cb + 1
        if text[end:end + 1] == "\n":
            end += 1
        replacement = None
        if target == "5" and kind == "MultiEffect":
            if (_prop_val(body, "shadowEnabled") or "") == "true":
                blur = _prop_val(body, "shadowBlur") or "1.0"
                lines = ["layer.effect: DropShadow {",
                         "    transparentBorder: true",
                         "    samples: 17",
                         "    radius: 8 * (%s)" % blur,
                         "    color: %s" % (_prop_val(body, "shadowColor") or '"black"'),
                         "    horizontalOffset: %s" % (_prop_val(body, "shadowHorizontalOffset") or "0"),
                         "    verticalOffset: %s" % (_prop_val(body, "shadowVerticalOffset") or "0"),
                         "}"]
                replacement = "\n".join(indent + l if i else l for i, l in enumerate(lines)) + "\n"
                need_ge = True
                log.append(tr("~ %s: MultiEffect-Schatten für Qt5 in DropShadow umgewandelt",
                              "~ %s: MultiEffect shadow converted to DropShadow for Qt5") % where)
            else:
                replacement = ""
                log.append(tr("~ %s: MultiEffect gibt es unter Qt5 nicht – Effekt entfernt",
                              "~ %s: MultiEffect does not exist on Qt5 – effect removed") % where)
        elif target == "5" and kind == "ShaderEffect" and ".qsb" in body:
            replacement = ""
            log.append(tr("~ %s: Qt6-Shader (.qsb) läuft nicht unter Qt5 – Effekt entfernt",
                          "~ %s: Qt6 shader (.qsb) does not work on Qt5 – effect removed") % where)
        elif target == "6" and kind == "ShaderEffect" and re.search(r"(gl_FragColor|varying|uniform\s)", body):
            replacement = ""
            log.append(tr("~ %s: Qt5-Shader (GLSL) läuft nicht unter Qt6 – Effekt entfernt",
                          "~ %s: Qt5 shader (GLSL) does not work on Qt6 – effect removed") % where)
        if replacement is not None:
            text = text[:ls] + (indent + replacement if replacement else "") + text[end:]
    return text, need_ge


def qt6_fixups(qml, log):
    """Weitere Anpassungen fuer Qt6: FontLoader.name ist dort nur lesbar."""
    m = mask(qml)
    for mo in reversed(list(re.finditer(r"(?<![\w.])FontLoader\s*\{", m))):
        m = mask(qml)
        ob = mo.end() - 1
        cb = match_brace(m, ob)
        body_t, body_m = qml[ob:cb + 1], m[ob:cb + 1]
        idm = re.search(r"\bid\s*:\s*(\w+)", body_m)
        nm = re.search(r"^[ \t]*name\s*:\s*(['\"][^'\"]*['\"])\s*;?[ \t]*\n?", body_t, re.M)
        if not nm or re.search(r"\bsource\s*:", body_m):
            continue
        lit = nm.group(1)
        new_body = body_t[:nm.start()] + body_t[nm.end():]
        qml = qml[:ob] + new_body + qml[cb + 1:]
        if idm:
            fid = idm.group(1)
            m2 = mask(qml)
            reps = [(x.start(), x.end()) for x in re.finditer(r"(?<![\w.])%s\s*\.\s*name\b" % re.escape(fid), m2)]
            for s_, e_ in reversed(reps):
                qml = qml[:s_] + lit + qml[e_:]
        log.append(tr("~ FontLoader: 'name' ist unter Qt6 nur lesbar – Schriftname %s direkt eingesetzt",
                      "~ FontLoader: 'name' is read-only on Qt6 – font name %s inserted directly") % lit)
    return qml


def convert_connections(text):
    """Wandelt 'onFoo: ...' in Connections-Objekten in 'function onFoo() {...}'
    um (Qt6-Schreibweise, funktioniert auch unter Qt 5.15)."""
    m = mask(text)
    starts = [mo.end() - 1 for mo in re.finditer(r"(?<![\w.])Connections\s*\{", m)]
    for ob in reversed(starts):
        m = mask(text)
        cb = match_brace(m, ob)
        mems = parse_members(text, m, ob + 1, cb)
        for mem in reversed(mems):
            if mem.kind != "handler" or "." in mem.name or not re.match(r"^on[A-Z]", mem.name):
                continue
            colon = mem.mtext.index(":")
            val = mem.text[colon + 1:].strip()
            if mask(val).lstrip().startswith("{"):
                body = val
            else:
                body = "{\n%s    %s;\n%s}" % (mem.indent, val.rstrip(";"), mem.indent)
            text = text[:mem.s] + "function %s() %s" % (mem.name, body) + text[mem.e:]
    return text


def _short(fid):
    s = re.sub(r"[^A-Za-z0-9]", "", fid.split("/")[-1].title())
    return s[:14] or "Src"


def safe_name(name):
    """Watchface-Name: nur a-z, 0-9 und '-', hoechstens 64 Zeichen."""
    name = re.sub(r"[^a-z0-9-]+", "-", str(name or "").strip().lower()).strip("-")
    return name[:64].strip("-")


def build(repo, project, write=True):
    """project = {
         "name": "mein-watchface",
         "base": "<face-id>",          # liefert Wurzeltyp & Wurzel-Einstellungen
         "items": [{"face": fid, "key": key, "code": optional_override}, ...]
       }
    Liefert dict(log=[...], qml=str, path=str)."""
    log = []
    name = safe_name(project.get("name") or "")
    if not name:
        raise ValueError(tr("Bitte einen Namen für das Watchface angeben (a-z, 0-9, -).", "Please enter a name for the watchface (a-z, 0-9, -)."))
    items = project.get("items") or []
    if not items:
        raise ValueError(tr("Es sind noch keine Bausteine ausgewählt.", "No building blocks selected yet."))
    faces = {}

    def get_face(fid):
        if fid not in faces:
            f = repo.face(fid)
            if f is None:
                raise ValueError(tr("Watchface '%s' nicht gefunden", "Watchface '%s' not found") % fid)
            faces[fid] = f
        return faces[fid]

    base_id = project.get("base") or items[0]["face"]
    base = get_face(base_id)
    target = str(project.get("target") or getattr(repo, "qt", "5"))
    if target not in ("5", "6"):
        target = "5"

    # 1) Auswahl je Quelle, Overrides
    order = []            # (face, block, code)
    chosen = {}           # fid -> set(keys)
    for it in items:
        f = get_face(it["face"])
        b = f.block_by_key.get(it["key"])
        if b is None:
            log.append(tr("! Baustein %s in %s nicht gefunden – übersprungen", "! Block %s in %s not found – skipped") % (it["key"], f.fid))
            continue
        if b.key in chosen.get(f.fid, ()):
            log.append(tr("! Baustein '%s' (%s) war doppelt – nur einmal übernommen",
                          "! Block '%s' (%s) was listed twice – used only once") % (b.label, f.fid))
            continue
        chosen.setdefault(f.fid, set()).add(b.key)
        order.append([f, b, it.get("code")])

    # 2) Abhaengigkeiten aufloesen (ids anderer Bausteine derselben Quelle)
    changed = True
    while changed:
        changed = False
        for f, b, code in list(order):
            refs = idents(mask(code)) if code else b.refs
            for r in refs:
                o = f.id_owner.get(r)
                if o is not None and o.key not in chosen[f.fid]:
                    chosen[f.fid].add(o.key)
                    # vor dem abhaengigen Baustein einsortieren
                    pos = next(i for i, x in enumerate(order) if x[1] is b)
                    order.insert(pos, [f, o, None])
                    log.append(tr("+ '%s' aus %s automatisch ergänzt (wird von '%s' benutzt)",
                                  "+ '%s' from %s added automatically (used by '%s')")
                               % (o.label, f.fid, b.label))
                    changed = True
    def ids_of_entry(ent):
        f, b, code = ent
        return ids_declared(mask(code)) if code else b.ids

    # Helfer automatisch dazu
    for fid in list(chosen):
        f = faces[fid]
        sel_ids = set()
        for ent in order:
            if ent[0].fid == fid:
                sel_ids |= ids_of_entry(ent)
        for hb in f.blocks:
            if hb.key in chosen[fid] or not hb.helper:
                continue
            if hb.refs & sel_ids:
                chosen[fid].add(hb.key)
                order.append([f, hb, None])
                log.append(tr("+ Helfer '%s' aus %s automatisch ergänzt (steuert gewählte Bausteine)",
                              "+ Helper '%s' from %s added automatically (controls selected blocks)")
                           % (hb.label, fid))

    # 3) Finale id-Mengen und fehlende ids je Quelle
    final_ids = {}
    missing = {}
    for fid in chosen:
        f = faces[fid]
        ids = set()
        for ent in order:
            if ent[0].fid == fid:
                ids |= ids_of_entry(ent)
        final_ids[fid] = ids
        missing[fid] = (f.all_ids - ids) - {f.root_id}

    # Helfer-Bausteine filtern
    for ent in order:
        f, b, code = ent
        if b.helper and code is None:
            nv = _filter_object(b.text, missing[f.fid], log, "'%s' (%s)" % (b.label, f.fid))
            if nv is None:
                ent[2] = False
            elif nv != b.text:
                ent[2] = nv
    order = [x for x in order if x[2] is not False]

    # 4) Wurzel-Handler je Quelle (gefiltert)
    #    + Wurzel-Properties/Funktionen per Abschluss
    src_order = []
    for f, b, c in order:
        if f.fid not in src_order:
            src_order.append(f.fid)
    if base.fid not in src_order:
        src_order.insert(0, base.fid)
        chosen.setdefault(base.fid, set())
        final_ids.setdefault(base.fid, set())
        missing.setdefault(base.fid, (base.all_ids) - {base.root_id})

    code_of = {}  # fid -> Liste Code-Texte (fuer Referenzsuche)
    for f, b, c in order:
        code_of.setdefault(f.fid, []).append(c if c else b.text)

    handler_parts = {}  # handler-name -> list of (fid, code)
    need_root = {fid: set() for fid in src_order}

    def scan_need(fid, code):
        f = faces[fid]
        mm = mask(code)
        refs = idents(mm) | root_refs(mm, f.root_id)
        return refs & f.rootish

    for fid in src_order:
        f = faces[fid]
        for code in code_of.get(fid, []):
            need_root[fid] |= scan_need(fid, code)
        if fid == base.fid:
            for a in f.assigns:
                need_root[fid] |= scan_need(fid, a.text)

    # Abschluss ueber Properties/Funktionen + Handler
    def close(fid):
        f = faces[fid]
        todo = list(need_root[fid])
        while todo:
            n = todo.pop()
            mem = f.props.get(n) or f.funcs.get(n) or f.signals.get(n)
            if mem is None:
                continue
            for x in scan_need(fid, mem.text):
                if x not in need_root[fid]:
                    need_root[fid].add(x)
                    todo.append(x)

    for fid in src_order:
        close(fid)
        f = faces[fid]
        for h in f.handlers:
            hn = h.name
            mo = re.match(r"^on([A-Z]\w*)Changed$", hn)
            if mo:
                pn = mo.group(1)[0].lower() + mo.group(1)[1:]
                if pn in f.props and pn not in need_root[fid]:
                    continue
            if fid != base.fid and not code_of.get(fid):
                continue
            colon = h.mtext.index(":")
            val = h.text[colon + 1:].strip()
            nv = _filter_statements(val, missing[fid], log, "%s (%s)" % (hn, fid))
            if nv is None:
                continue
            handler_parts.setdefault(hn, []).append((fid, nv))
            need_root[fid] |= scan_need(fid, nv)
        close(fid)

    # 5) Namenskonflikte: ids und Wurzel-Properties
    used_ids = {"root"}
    used_root = {}   # name -> normalisierter Text
    id_map = {}
    prop_map = {}
    for fid in src_order:
        f = faces[fid]
        idm = {}
        for i in sorted(final_ids.get(fid, ())):
            if i in used_ids:
                n = i + "_" + _short(fid)
                k = 2
                while n in used_ids:
                    n = "%s_%s%d" % (i, _short(fid), k)
                    k += 1
                idm[i] = n
                log.append(tr("~ id '%s' aus %s heißt jetzt '%s' (Namenskonflikt)", "~ id '%s' from %s renamed to '%s' (name conflict)") % (i, fid, n))
            used_ids.add(idm.get(i, i))
        id_map[fid] = idm
        pm = {}
        for n in sorted(need_root[fid]):
            mem = f.props.get(n) or f.funcs.get(n) or f.signals.get(n)
            norm = " ".join(replace_root_id(mem.text, f.root_id, "root").split())
            if n in used_root and used_root[n] != norm:
                nn = n + "_" + _short(fid)
                pm[n] = nn
                log.append(tr("~ Property/Funktion '%s' aus %s heißt jetzt '%s' (unterschiedliche Definition)",
                              "~ Property/function '%s' from %s renamed to '%s' (different definition)")
                           % (n, fid, nn))
            elif n in used_ids and n not in used_root:
                nn = n + "_" + _short(fid)
                pm[n] = nn
        for n in sorted(need_root[fid]):
            mem = f.props.get(n) or f.funcs.get(n) or f.signals.get(n)
            norm = " ".join(replace_root_id(mem.text, f.root_id, "root").split())
            used_root.setdefault(pm.get(n, n), norm)
        prop_map[fid] = pm

    def xform(fid, code, local_props=()):
        f = faces[fid]
        code = rename_idents(code, id_map[fid], f.root_id, prop_map[fid], local_props)
        return replace_root_id(code, f.root_id, "root")

    # 6) QML zusammensetzen
    ind = "    "
    out = []
    headers = []
    for fid in src_order:
        h = faces[fid].header
        if h and h not in headers:
            headers.append(h)
    out.append(tr("// Erzeugt mit wfmix aus: ", "// Generated with wfmix from: ") + ", ".join(src_order))
    out.append(tr("// Basis: ", "// Base: ") + base.fid + "  ·  " + tr("Ziel: Qt", "Target: Qt") + target)
    for h in headers:
        out.append("")
        out.append(h)
    out.append("")
    pragmas = []
    for fid in src_order:
        for p in faces[fid].pragmas:
            if p not in pragmas:
                pragmas.append(p)
    out.extend(pragmas)
    extra_imports = []
    out.append("\x00IMPORTS")
    out.append("")
    out.append("%s {" % base.root_type)
    out.append(ind + "id: root")
    out.append("")

    seen_root = set()
    for fid in src_order:
        f = faces[fid]
        # Reihenfolge wie im Original
        for mem in f.members:
            if mem.kind in ("property", "function", "signal", "component") and mem.name in need_root[fid]:
                nn = prop_map[fid].get(mem.name, mem.name)
                if nn in seen_root:
                    continue
                seen_root.add(nn)
                txt = mem.text
                if nn != mem.name:
                    txt = re.sub(r"\b%s\b" % re.escape(mem.name), nn, txt, count=1)
                if mem.kind == "function":
                    ft = _filter_statements(txt, missing[fid], log, tr("Funktion %s (%s)", "function %s (%s)") % (mem.name, fid))
                    if ft is not None:
                        txt = ft
                txt = xform(fid, txt)
                if mem.lead:
                    out.extend(ind + l for l in mem.lead.split("\n"))
                out.append(ind + txt)
    out.append("")
    for a in base.assigns:
        if a.lead:
            out.extend(ind + l for l in a.lead.split("\n"))
        at, ge = adapt_effects(xform(base.fid, a.text), target, log, base.fid)
        if ge:
            extra_imports.append("import QtGraphicalEffects 1.15")
        if at.strip():
            out.append(ind + at)
    # Handler zusammenfuehren
    for hn, parts in handler_parts.items():
        new_hn = hn
        bodies = []
        for fid, code in parts:
            mo = re.match(r"^on([A-Z]\w*)Changed$", hn)
            if mo:
                pn = mo.group(1)[0].lower() + mo.group(1)[1:]
                if pn in prop_map[fid]:
                    nn = prop_map[fid][pn]
                    new_hn = "on" + nn[0].upper() + nn[1:] + "Changed"
            code = xform(fid, code)
            cm = mask(code)
            if cm.lstrip().startswith("{"):
                ob = cm.find("{")
                cb = match_brace(cm, ob)
                inner = code[ob + 1:cb]
                lines = inner.strip("\n").rstrip().split("\n")
                # Einrueckung normalisieren
                mins = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
                body = "\n".join(ind * 2 + l[mins:] if l.strip() else "" for l in lines)
            else:
                body = ind * 2 + code.strip().rstrip(";") + ";"
            if len(parts) > 1:
                body = ind * 2 + tr("// aus ", "// from ") + fid + "\n" + body
            bodies.append(body)
        out.append(ind + new_hn + ": {")
        out.append("\n\n".join(bodies))
        out.append(ind + "}")
    out.append("")
    for f, b, code in order:
        t = code if code else b.text
        t = xform(f.fid, t, b.local_props)
        t, ge = adapt_effects(t, target, log, "'%s' (%s)" % (b.label, f.fid))
        if ge:
            extra_imports.append("import QtGraphicalEffects 1.15")
        only = qt_only(t)
        if only and only != target:
            log.append(tr("! '%s' (%s) läuft nur unter Qt%s – auf Qt%s fehlt dieser Teil oder erzeugt Fehler",
                          "! '%s' (%s) only works on Qt%s – on Qt%s this part is missing or causes errors")
                       % (b.label, f.fid, only, target))
        if target == "6":
            t = convert_connections(t)
        lead = b.lead if not code else ""
        out.append(ind + "// ---- %s  [%s]" % (b.label, f.fid))
        if lead:
            out.extend(ind + l for l in lead.split("\n"))
        out.append(ind + t)
        out.append("")
    while out and out[-1] == "":
        out.pop()
    out.append("}")
    imports = emit_imports([faces[fid].imports for fid in src_order] + [extra_imports], target, log)
    out = [x for y in out for x in (imports if y == "\x00IMPORTS" else [y])]
    qml = "\n".join(out) + "\n"
    if target == "6":
        qml = qt6_fixups(qml, log)

    path = os.path.join(repo.workdir, name)
    res = {"log": log, "qml": qml, "path": path, "name": name, "target": target}
    if write:
        write_face(repo, name, qml, src_order, faces, base, project, log)
    return res


def write_face(repo, name, qml, src_order, faces, base, project, log):
    path = os.path.join(repo.workdir, name)
    share = os.path.join(path, "usr", "share")
    wfd = os.path.join(path, WF_SUBDIR)
    # alten Stand entfernen (nur usr/share)
    if os.path.isdir(share):
        shutil.rmtree(share)
    os.makedirs(wfd, exist_ok=True)
    with open(os.path.join(wfd, name + ".qml"), "w", encoding="utf-8") as fp:
        fp.write(qml)
    for fid in src_order:
        f = faces[fid]
        src_share = os.path.join(f.dir, "usr", "share")
        comps = component_files(os.path.join(f.dir, WF_SUBDIR))
        for dp, dn, fn in os.walk(src_share):
            rel = os.path.relpath(dp, src_share)
            parts = rel.split(os.sep)
            if parts[0] == "asteroid-launcher" and len(parts) > 1 and parts[1] == "watchfaces-preview":
                continue
            for fname in fn:
                if fname.startswith("."):
                    continue
                if rel == os.path.join("asteroid-launcher", "watchfaces"):
                    # nur Hilfsdateien (Komponenten mit Grossbuchstaben, .js)
                    if fname.endswith(".qml") and fname not in comps:
                        continue
                    if fname.endswith(".qrc"):
                        continue
                dst_dir = os.path.join(share, rel)
                os.makedirs(dst_dir, exist_ok=True)
                dst = os.path.join(dst_dir, fname)
                if not os.path.exists(dst):
                    shutil.copy2(os.path.join(dp, fname), dst)
    # Vorschau-Bilder der Basis unter neuem Namen
    prev = os.path.join(base.dir, "usr", "share", "asteroid-launcher", "watchfaces-preview")
    if os.path.isdir(prev):
        for res in os.listdir(prev):
            srcp = os.path.join(prev, res, base.qml_name + ".png")
            if os.path.isfile(srcp):
                d = os.path.join(share, "asteroid-launcher", "watchfaces-preview", res)
                os.makedirs(d, exist_ok=True)
                shutil.copy2(srcp, os.path.join(d, name + ".png"))
    proj = dict(project)
    proj["name"] = name
    proj["target"] = str(project.get("target") or getattr(repo, "qt", "5"))
    with open(os.path.join(path, "wfmix-projekt.json"), "w", encoding="utf-8") as fp:
        json.dump(proj, fp, indent=2, ensure_ascii=False)
    log.append(tr("= geschrieben: ", "= written: ") + os.path.join(wfd, name + ".qml"))


def load_project(repo, name):
    p = os.path.join(repo.workdir, safe_name(name), "wfmix-projekt.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fp:
        return json.load(fp)


def list_projects(repo):
    res = []
    if os.path.isdir(repo.workdir):
        for n in sorted(os.listdir(repo.workdir)):
            if os.path.isfile(os.path.join(repo.workdir, n, "wfmix-projekt.json")):
                res.append(n)
    return res
