#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wfmix - Watchface-Baukasten fuer AsteroidOS (Qt5 und Qt6)
wfmix - Watchface construction kit for AsteroidOS (Qt5 and Qt6)

Startet eine lokale Oberflaeche im Browser, mit der man Bausteine
(Zeiger, Ziffern, Datum, Akku-Anzeige, ...) aus beliebigen Watchfaces des
unofficial-watchfaces-Repos zu einem neuen Watchface kombinieren, Bausteine
loeschen oder bearbeiten, am PC testen und per SSH auf die Uhr laden kann.
Das Ergebnis kann fuer Qt5 (AsteroidOS <= 2.1) oder Qt6 (AsteroidOS >= 2.2)
erzeugt werden - unabhaengig davon, welcher Repo-Stand ausgecheckt ist.

Aufruf / usage (im Ordner unofficial-watchfaces/wfmix oder mit --repo):
    python3 wfmix.py                 # Oberflaeche starten / start UI
    python3 wfmix.py --help          # alle Optionen / all options

Kommandozeile / command line:
    python3 wfmix.py list
    python3 wfmix.py blocks analog-commander
    python3 wfmix.py build mixed/my-face/wfmix-projekt.json
    python3 wfmix.py test  my-face
    python3 wfmix.py deploy my-face
"""

import argparse
import collections
import io
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wfmix_core as core  # noqa: E402
from wfmix_core import tr  # noqa: E402

VERSION = "2.0"

# Git-Staende von unofficial-watchfaces fuer den Test der jeweils anderen Qt-Version
QT5_REFS = ("2c605dc",)
QT6_REFS = ("origin/master", "master", "origin/main", "main")

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "wfmix")
ENV_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Log-Puffer (wird von der Oberflaeche abgefragt)
# --------------------------------------------------------------------------

class Log(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.lines = collections.deque(maxlen=2000)
        self.n = 0

    def add(self, text, kind="info"):
        with self.lock:
            for line in str(text).rstrip("\n").split("\n"):
                self.n += 1
                self.lines.append((self.n, kind, line))
        if CONSOLE:
            print(text)

    def since(self, k):
        with self.lock:
            return [{"n": n, "k": kind, "t": t} for n, kind, t in self.lines if n > k]


CONSOLE = False
LOG = Log()


# --------------------------------------------------------------------------
# Test am PC: Qt5 (qmlscene) oder Qt6 (qml)
# --------------------------------------------------------------------------

# Meldungen, die beim Test immer kommen und nichts bedeuten
NOISE = ("XDG_RUNTIME_DIR", "does not exist.", 'Invalid URL: ""', "Implicitly defined onFoo",
         "propagateSizeHints", "Could not find the Qt platform plugin \"wayland\"",
         "qt.qpa.", "Wayland does not support")

APT5 = ("sudo apt install qmlscene qml-module-qtquick2 qml-module-qtquick-controls2 "
        "qml-module-qtquick-layouts qml-module-qtgraphicaleffects qml-module-qt-labs-settings "
        "qml-module-qtquick-dialogs qml-module-qtquick-shapes qml-module-qtquick-window2")
APT6 = ("sudo apt install qml-qt6 qml6-module-qtquick qml6-module-qtquick-controls "
        "qml6-module-qtquick-layouts qml6-module-qt5compat-graphicaleffects qml6-module-qtquick-shapes "
        "qml6-module-qtquick-window qml6-module-qtcore qml6-module-qtquick-templates "
        "qml6-module-qtqml-workerscript qml6-module-qt-labs-settings qml6-module-qtquick-dialogs "
        "libqt6svg6 qt6-image-formats-plugins")

EXTRA_CONTEXT = {
    "compositor": """QtObject {
    id: compositor
    property bool amb: appRoot.displayAmbient
    signal displayAmbientEntered()
    signal displayAmbientLeft()
    signal displayAmbientChanged()
    onAmbChanged: { displayAmbientChanged(); if (amb) displayAmbientEntered(); else displayAmbientLeft() }
}""",
    "desktop": """QtObject {
    id: desktop
    property bool displayAmbient: appRoot.displayAmbient
}""",
    "localeManager": """QtObject {
    id: localeManager
    property string changesObserver: ""
}""",
    "burnInProtectionManager": """QtObject {
    id: burnInProtectionManager
    property real widthOffset: 0
    property real heightOffset: 0
}""",
}


def _which(*names, extra=()):
    for b in names:
        p = shutil.which(b)
        if p:
            return p
    for p in extra:
        if os.path.exists(p):
            return p
    return None


def qml_binary(qt):
    if qt == "6":
        return _which("qml6", "qml-qt6",
                      extra=("/usr/lib/qt6/bin/qml", "/usr/lib/x86_64-linux-gnu/qt6/bin/qml",
                             "/usr/lib/aarch64-linux-gnu/qt6/bin/qml"))
    return _which("qmlscene", "qmlscene-qt5",
                  extra=("/usr/lib/qt5/bin/qmlscene", "/usr/lib/x86_64-linux-gnu/qt5/bin/qmlscene",
                         "/usr/lib/aarch64-linux-gnu/qt5/bin/qmlscene"))


_QT6_VER = {}


def qt6_version(binary):
    """(major, minor) der Qt6-qml-Laufzeit, z.B. (6, 4)."""
    if binary not in _QT6_VER:
        ver = (6, 99)
        try:
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
            out = subprocess.run([binary, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, timeout=15, env=env).stdout
            mo = re.search(r"\b6\.(\d+)", out)
            if mo:
                ver = (6, int(mo.group(1)))
        except (OSError, subprocess.TimeoutExpired):
            pass
        _QT6_VER[binary] = ver
    return _QT6_VER[binary]


def _git(repo, *args):
    try:
        p = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=60)
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class Runner(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.fontconf = None
        self.tmpdirs = []
        self.lock = threading.Lock()

    # -- Testumgebung je Qt-Version -------------------------------------
    def test_env(self, qt):
        """Liefert (loader.qml, fake-components-Ordner) fuer die Qt-Version.
        Stammt der ausgecheckte Repo-Stand von der anderen Version, werden die
        Dateien aus der Git-Historie geholt (git archive)."""
        repo = self.cfg["repo"]
        if core.detect_repo_qt(repo) == qt:
            return os.path.join(repo, "loader.qml"), os.path.join(repo, "fake-components")
        refs = QT6_REFS if qt == "6" else QT5_REFS
        for ref in refs:
            dest = os.path.join(CACHE, "qt%s-%s" % (qt, re.sub(r"[^\w.-]", "_", ref)))
            data = _git(repo, "show", "%s:loader.qml" % ref)
            if not data:
                continue
            is6 = b"WatchfaceConfig" in data
            if is6 != (qt == "6"):
                continue
            with ENV_LOCK:
                if os.path.isfile(os.path.join(dest, "loader.qml")) and \
                        open(os.path.join(dest, "loader.qml"), "rb").read() == data:
                    return os.path.join(dest, "loader.qml"), os.path.join(dest, "fake-components")
                tar = _git(repo, "archive", ref, "loader.qml", "fake-components")
                if not tar:
                    continue
                if os.path.isdir(dest):
                    shutil.rmtree(dest, ignore_errors=True)
                os.makedirs(dest, exist_ok=True)
                with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
                    try:
                        tf.extractall(dest, filter="data")
                    except TypeError:  # Python < 3.12
                        tf.extractall(dest)
            return os.path.join(dest, "loader.qml"), os.path.join(dest, "fake-components")
        return None, None

    def available(self, qt):
        if not qml_binary(qt):
            return False
        loader, _ = self.test_env(qt)
        return bool(loader)

    def _imports(self, fake, qt):
        """fake-components + wfmix-Stubs zu einem Import-Ordner zusammenfuehren."""
        os.makedirs(CACHE, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="imports-qt%s-" % qt, dir=CACHE)
        self.tmpdirs.append(tmp)
        d = os.path.join(tmp, "imports")
        shutil.copytree(fake, d)
        stubs = os.path.join(HERE, "stubs")
        for dp, dn, fn in os.walk(stubs):
            if "qmldir" not in fn:
                continue
            rel = os.path.relpath(dp, stubs)
            tgt = os.path.join(d, rel)
            os.makedirs(tgt, exist_ok=True)
            with open(os.path.join(dp, "qmldir"), encoding="utf-8") as fp:
                stub_lines = [l.strip() for l in fp if l.strip()]
            qmldir = os.path.join(tgt, "qmldir")
            have = ""
            if os.path.exists(qmldir):
                with open(qmldir, encoding="utf-8") as fp:
                    have = fp.read()
            types = set(re.findall(r"^(?:singleton\s+)?(\w+)\s", have, re.M))
            add = []
            for l in stub_lines:
                if l.startswith("module"):
                    if not have:
                        add.append(l)
                    continue
                mo = re.match(r"^(?:singleton\s+)?(\w+)\s", l)
                if mo and mo.group(1) in types:
                    continue
                add.append(l)
                fname = l.split()[-1]
                if not os.path.exists(os.path.join(tgt, fname)):
                    shutil.copy2(os.path.join(dp, fname), os.path.join(tgt, fname))
            if add:
                with open(qmldir, "a", encoding="utf-8") as fp:
                    if have and not have.endswith("\n"):
                        fp.write("\n")
                    fp.write("\n".join(add) + "\n")
        return d

    def _patched_loader(self, loader, qt, binary=None):
        with open(loader, encoding="utf-8") as fp:
            t = fp.read()
        # Settings liegt erst ab Qt 6.5 in QtCore; aeltere Qt6 (z.B. Ubuntu 24.04: 6.4)
        # haben es nur in Qt.labs.settings
        if qt == "6" and binary and qt6_version(binary) < (6, 5) and re.search(r"^import QtCore\s*$", t, re.M):
            t = re.sub(r"^import QtCore\s*$", "import Qt.labs.settings 1.0", t, flags=re.M)
        missing = [k for k in EXTRA_CONTEXT if not re.search(r"\bid:\s*%s\b" % k, t)]
        if missing:
            lines = t.split("\n")
            idx = next((i for i, l in enumerate(lines) if re.search(r"\bid:\s*use12H\b", l)), None)
            if idx is not None:
                j = idx
                while j > 0 and not lines[j].strip().endswith("{"):
                    j -= 1
                ind = re.match(r"\s*", lines[j]).group(0)
                block = []
                for k in missing:
                    block += [ind + x for x in EXTRA_CONTEXT[k].split("\n")] + [""]
                lines[j:j] = block
                t = "\n".join(lines)
        tmp = tempfile.mkdtemp(prefix="loader-", dir=CACHE)
        self.tmpdirs.append(tmp)
        p = os.path.join(tmp, "loader.qml")
        with open(p, "w", encoding="utf-8") as fp:
            fp.write(t)
        return p

    # -- Prozess ----------------------------------------------------------
    def stop(self):
        with self.lock:
            p = self.proc
            self.proc = None
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self._cleanup()

    def _cleanup(self):
        if self.fontconf and os.path.exists(self.fontconf):
            try:
                os.remove(self.fontconf)
            except OSError:
                pass
        self.fontconf = None
        for d in self.tmpdirs:
            shutil.rmtree(d, ignore_errors=True)
        self.tmpdirs = []

    def test(self, face_dir, name, qt="5", wait=False, timeout=None):
        qs = qml_binary(qt)
        if not qs:
            LOG.add(tr("Für den Test mit Qt%s fehlt das Programm. Installieren mit:\n  %s",
                       "The program for testing with Qt%s is missing. Install with:\n  %s")
                    % (qt, APT6 if qt == "6" else APT5), "err")
            return None
        loader, fake = self.test_env(qt)
        if not loader:
            LOG.add(tr("Für den Test mit Qt%s fehlen loader.qml/fake-components. Der Repo-Ordner muss ein "
                       "Git-Klon von unofficial-watchfaces sein (mit Stand %s).",
                       "loader.qml/fake-components for testing with Qt%s are missing. The repo folder must be "
                       "a git clone of unofficial-watchfaces (containing %s).")
                    % (qt, "master" if qt == "6" else "2c605dc"), "err")
            return None
        self.stop()
        imports = self._imports(fake, qt)
        loader = self._patched_loader(loader, qt, qs)
        fonts = os.path.join(face_dir, "usr", "share", "fonts")
        if os.path.isdir(fonts):
            base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                "fontconfig", "conf.d")
            os.makedirs(base, exist_ok=True)
            fd, path = tempfile.mkstemp(prefix="9999-wfmix-", suffix=".conf", dir=base)
            with os.fdopen(fd, "w") as fp:
                fp.write('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
                         "<fontconfig><dir>%s/</dir></fontconfig>\n" % fonts)
            self.fontconf = path
        wall = self.cfg.get("wallpaper") or os.path.join(HERE, "stubs", "background.png")
        wfdir = os.path.join(face_dir, core.WF_SUBDIR) + "/"
        env = dict(os.environ)
        if qt == "6":
            cfgdir = tempfile.mkdtemp(prefix="wfmix-cfg-")
            self.tmpdirs.append(cfgdir)
            mod = os.path.join(cfgdir, "WatchfaceConfig")
            os.makedirs(mod)
            with open(os.path.join(mod, "qmldir"), "w") as fp:
                fp.write("module WatchfaceConfig\nsingleton WatchfaceConfig 1.0 WatchfaceConfig.qml\n")
            with open(os.path.join(mod, "WatchfaceConfig.qml"), "w", encoding="utf-8") as fp:
                fp.write('pragma Singleton\nimport QtQuick 2.15\n\nQtObject {\n'
                         '    readonly property string watchfaceName: %s\n'
                         '    readonly property string backgroundImage: %s\n'
                         '    readonly property string backgroundRoundImage: %s\n'
                         '    readonly property string relativeRootDir: %s\n}\n'
                         % (json.dumps(name), json.dumps(wall), json.dumps(wall), json.dumps(wfdir)))
            env["QML_IMPORT_PATH"] = imports + os.pathsep + cfgdir
            cmd = [qs, "-I", imports, "-I", cfgdir, loader]
        else:
            cmd = [qs, name, wall, wall, wfdir, loader, "-I", imports]
        LOG.add(tr("▶ Teste %s am PC (Qt%s) …", "▶ Testing %s on the PC (Qt%s) …") % (name, qt), "head")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             cwd=face_dir, env=env, text=True, errors="replace",
                             start_new_session=True)
        with self.lock:
            self.proc = p
        out = []

        def pump():
            for line in p.stdout:
                line = line.rstrip()
                if not line or any(x in line for x in NOISE):
                    continue
                line = line.replace("file://" + wfdir, "")
                out.append(line)
                kind = "warn"
                if any(k in line for k in ("is not a type", "Error", "error", "not installed",
                                           "Cannot assign", "not defined", "set multiple times",
                                           "Expected token", "Unexpected", "is not a function")):
                    kind = "err"
                LOG.add("   " + line, kind)
            rc = p.wait()
            LOG.add(tr("■ Testfenster geschlossen (Code %s)", "■ Test window closed (code %s)") % rc, "head")
            if self.proc is p:
                self._cleanup()

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        if wait:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGTERM)
            t.join(5)
            self._cleanup()
            return out
        return p


# --------------------------------------------------------------------------
# Uhr (SSH, Developer-Mode)
# --------------------------------------------------------------------------

def _ssh_base(cfg):
    return ["-o", "ConnectTimeout=6", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new", "-p", str(cfg["port"])]


def run_cmd(cmd, title=None):
    if title:
        LOG.add("  $ " + title, "cmd")
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        LOG.add(tr("  Zeitüberschreitung", "  Timeout"), "err")
        return False, ""
    except OSError as e:
        LOG.add("  %s" % e, "err")
        return False, ""
    for line in p.stdout.splitlines():
        if "Pseudo-terminal" in line or not line.strip():
            continue
        LOG.add("    " + line, "warn" if p.returncode == 0 else "err")
    if p.returncode != 0:
        LOG.add(tr("  Fehler (Code %d)", "  Error (code %d)") % p.returncode, "err")
    return p.returncode == 0, p.stdout


def ssh(cfg, user, command):
    cmd = ["ssh"] + _ssh_base(cfg) + ["%s@%s" % (user, cfg["host"]), command]
    return run_cmd(cmd, "ssh %s@%s %s" % (user, cfg["host"], command))


def deploy(cfg, face_dir, name, restart=True, activate=True):
    host = cfg["host"]
    share = os.path.join(face_dir or "", "usr", "share")
    if not face_dir or not os.path.isdir(share):
        LOG.add(tr("Watchface %s ist noch nicht erzeugt.", "Watchface %s has not been generated yet.") % name, "err")
        return False
    LOG.add(tr("▶ Lade %s auf die Uhr (%s) …", "▶ Copying %s to the watch (%s) …") % (name, host), "head")
    entries = [os.path.join(share, e) for e in sorted(os.listdir(share))]
    cmd = ["scp", "-r", "-P", str(cfg["port"]), "-o", "ConnectTimeout=6", "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=accept-new"] + entries + ["root@%s:/usr/share/" % host]
    ok, _ = run_cmd(cmd, "scp -r … root@%s:/usr/share/" % host)
    if not ok:
        LOG.add(tr("Kopieren fehlgeschlagen. Ist die Uhr per USB verbunden und unter Einstellungen → USB "
                   "der Developer-Modus aktiv? Klappt 'ssh root@%s' im Terminal?",
                   "Copying failed. Is the watch connected via USB with Developer mode enabled under "
                   "Settings → USB? Does 'ssh root@%s' work in a terminal?") % host, "err")
        return False
    if activate:
        ssh(cfg, "ceres", "dconf write /desktop/asteroid/watchface "
            "\"'file:///usr/share/asteroid-launcher/watchfaces/%s.qml'\"" % name)
    if restart:
        LOG.add(tr("  Starte die Oberfläche der Uhr neu (neue Schriften/Bilder werden geladen) …",
                   "  Restarting the watch UI (loads new fonts/images) …"))
        ssh(cfg, "root", "systemctl restart user@1000")
    LOG.add(tr("■ Fertig – %s ist auf der Uhr%s.", "■ Done – %s is on the watch%s.")
            % (name, tr(" und aktiv", " and active") if activate else ""), "ok")
    return True


def undeploy(cfg, name):
    LOG.add(tr("▶ Entferne %s von der Uhr …", "▶ Removing %s from the watch …") % name, "head")
    ok, _ = ssh(cfg, "root", "rm -f /usr/share/asteroid-launcher/watchfaces/%s.qml "
                "/usr/share/asteroid-launcher/watchfaces-preview/*/%s.png" % (name, name))
    if ok:
        LOG.add(tr("■ Entfernt. Bilder und Schriften bleiben liegen (können von anderen Watchfaces genutzt werden).",
                   "■ Removed. Images and fonts stay (other watchfaces may use them)."), "ok")
    return ok


def watch_info(cfg):
    LOG.add(tr("▶ Prüfe Verbindung zur Uhr …", "▶ Checking connection to the watch …"), "head")
    ok, out = ssh(cfg, "root", "grep PRETTY_NAME /etc/os-release; ls /usr/lib | grep -m1 -E 'libQt[56]Core'")
    if ok:
        qt = "6" if "libQt6Core" in out else "5" if "libQt5Core" in out else ""
        if qt:
            LOG.add(tr("■ Die Uhr nutzt Qt%s → als Ziel „Qt%s“ wählen.",
                       "■ The watch uses Qt%s → choose target \"Qt%s\".") % (qt, qt), "ok")
            cfg["watch_qt"] = qt
    return ok


# --------------------------------------------------------------------------
# Web-Oberflaeche
# --------------------------------------------------------------------------

def thumb_path(repo, qml_name):
    for suf in ("-round.webp", ".webp", "-round.png", ".png", "-round.jpg", ".jpg"):
        p = os.path.join(repo.path, ".thumbnails", qml_name + suf)
        if os.path.isfile(p):
            return p
    return None


def own_preview(repo, f):
    d = os.path.join(f.dir, "usr", "share", "asteroid-launcher", "watchfaces-preview")
    if os.path.isdir(d):
        for res in sorted(os.listdir(d), key=lambda x: -int(x) if x.isdigit() else 0):
            p = os.path.join(d, res, f.qml_name + ".png")
            if os.path.isfile(p):
                return p
    return None


def face_dir_for(repo, name):
    for base in (repo.workdir, repo.path):
        d = os.path.join(base, name)
        if os.path.isdir(os.path.join(d, core.WF_SUBDIR)):
            return d
    return None


def own_target(repo, name, default):
    p = core.load_project(repo, name)
    return str((p or {}).get("target") or default)


def make_handler(repo, cfg, runner):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Browser hat die Anfrage abgebrochen

        def _file(self, path):
            if not path or not os.path.isfile(path):
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if path.endswith(".webp"):
                ctype = "image/webp"
            with open(path, "rb") as fp:
                self._send(200, fp.read(), ctype)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/":
                    with open(os.path.join(HERE, "wfmix.html"), encoding="utf-8") as fp:
                        return self._send(200, fp.read(), "text/html; charset=utf-8")
                if u.path == "/api/state":
                    faces = []
                    for f in repo.faces():
                        d = f.to_json()
                        d["own"] = getattr(f, "own", False)
                        if d["own"]:
                            d["qt"] = own_target(repo, f.qml_name, f.qt)
                        faces.append(d)
                    return self._send(200, {
                        "version": VERSION, "repo": repo.path, "work": repo.workdir,
                        "repoQt": repo.qt, "lang": core.LANG,
                        "host": cfg["host"], "watchQt": cfg.get("watch_qt", ""),
                        "testers": {"5": bool(qml_binary("5")), "6": bool(qml_binary("6"))},
                        "ssh": bool(shutil.which("ssh")), "faces": faces,
                        "projects": core.list_projects(repo), "errors": repo.errors,
                    })
                if u.path == "/api/face":
                    f = repo.face(q.get("id", ""))
                    if not f:
                        return self._send(404, {"error": tr("Watchface nicht gefunden", "Watchface not found")})
                    d = f.to_json(with_blocks=True)
                    d["own"] = getattr(f, "own", False)
                    return self._send(200, d)
                if u.path == "/api/thumb":
                    f = repo.face(q.get("id", ""))
                    p = None
                    if f:
                        p = thumb_path(repo, f.qml_name) or own_preview(repo, f)
                    return self._file(p)
                if u.path == "/api/project":
                    p = core.load_project(repo, q.get("name", ""))
                    return self._send(200 if p else 404,
                                      p or {"error": tr("Projekt nicht gefunden", "Project not found")})
                if u.path == "/api/log":
                    return self._send(200, LOG.since(int(q.get("since", "0") or 0)))
                if u.path == "/api/source":
                    f = repo.face(q.get("id", ""))
                    return self._send(200, f.text if f else "", "text/plain; charset=utf-8")
                return self._send(404, {"error": "unknown"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa
                return self._send(500, {"error": str(e)})

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            try:
                if u.path == "/api/lang":
                    if data.get("lang") in ("de", "en"):
                        core.LANG = data["lang"]
                    return self._send(200, {"lang": core.LANG})
                if u.path in ("/api/build", "/api/preview"):
                    write = u.path == "/api/build"
                    res = core.build(repo, data.get("project") or {}, write=write)
                    if write:
                        LOG.add(tr("▶ Watchface '%s' erzeugt (Qt%s)", "▶ Watchface '%s' generated (Qt%s)")
                                % (res["name"], res["target"]), "head")
                        for line in res["log"]:
                            LOG.add("  " + line, "warn" if line.startswith("!") else "info")
                    return self._send(200, res)
                if u.path == "/api/test":
                    proj = data.get("project")
                    if proj:
                        res = core.build(repo, proj, write=True)
                        for line in res["log"]:
                            LOG.add("  " + line, "warn" if line.startswith("!") else "info")
                        name, qt = res["name"], res["target"]
                    else:
                        name, qt = data.get("name"), repo.qt
                        if not data.get("dir"):
                            qt = own_target(repo, name, repo.qt)
                    d = face_dir_for(repo, data.get("dir") or name)
                    if not d:
                        return self._send(404, {"error": tr("nicht gefunden", "not found")})
                    runner.test(d, name, qt)
                    return self._send(200, {"ok": True, "name": name, "qt": qt})
                if u.path == "/api/stop":
                    runner.stop()
                    return self._send(200, {"ok": True})
                if u.path == "/api/deploy":
                    proj = data.get("project")
                    if data.get("host"):
                        cfg["host"] = data["host"]
                    if proj:
                        res = core.build(repo, proj, write=True)
                        name = res["name"]
                    else:
                        name = data.get("name")
                    d = face_dir_for(repo, name)
                    threading.Thread(target=deploy, args=(cfg, d, name, data.get("restart", True)),
                                     daemon=True).start()
                    return self._send(200, {"ok": True})
                if u.path == "/api/undeploy":
                    if data.get("host"):
                        cfg["host"] = data["host"]
                    threading.Thread(target=undeploy, args=(cfg, core.safe_name(data.get("name", ""))),
                                     daemon=True).start()
                    return self._send(200, {"ok": True})
                if u.path == "/api/watchinfo":
                    if data.get("host"):
                        cfg["host"] = data["host"]
                    threading.Thread(target=watch_info, args=(cfg,), daemon=True).start()
                    return self._send(200, {"ok": True})
                if u.path == "/api/open":
                    name = core.safe_name(data.get("name", ""))
                    p = os.path.join(repo.workdir, name, core.WF_SUBDIR, name + ".qml")
                    if os.path.isfile(p):
                        opener = shutil.which("xdg-open") or shutil.which("open")
                        if opener:
                            subprocess.Popen([opener, p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        LOG.add(tr("Geöffnet: ", "Opened: ") + p)
                    return self._send(200, {"ok": True, "path": p})
                return self._send(404, {"error": "unknown"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except ValueError as e:
                LOG.add("✖ " + str(e), "err")
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa
                import traceback
                LOG.add(tr("✖ Interner Fehler: %s", "✖ Internal error: %s") % e, "err")
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
    return H


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def find_repo(arg):
    cands = [arg] if arg else [os.getcwd(), os.path.dirname(HERE), HERE]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "loader.qml")) and os.path.isdir(os.path.join(c, "fake-components")):
            return os.path.abspath(c)
    return None


def default_lang():
    for k in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        v = os.environ.get(k, "")
        if v:
            return "de" if v.lower().startswith("de") else "en"
    return "en"


def main():
    global CONSOLE
    ap = argparse.ArgumentParser(description="wfmix – Watchface-Baukasten / watchface construction kit "
                                             "for AsteroidOS (Qt5 + Qt6)")
    ap.add_argument("command", nargs="?", default="ui",
                    help="ui (default), list, blocks FACE, build PROJECT.json, test NAME, deploy NAME")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--repo", help="path to unofficial-watchfaces (default: auto)")
    ap.add_argument("--work", help="folder for your own watchfaces (default: <repo>/mixed)")
    ap.add_argument("--host", default="192.168.2.15", help="watch IP (default 192.168.2.15)")
    ap.add_argument("--port", type=int, default=22, help="SSH port of the watch")
    ap.add_argument("--ui-port", type=int, default=8777, help="port of the web UI")
    ap.add_argument("--wall", help="background image for the PC test")
    ap.add_argument("--target", choices=("5", "6"), help="Qt version for 'test' (default: from project)")
    ap.add_argument("--lang", choices=("de", "en"), help="language / Sprache (default: system)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    a = ap.parse_args()

    core.LANG = a.lang or default_lang()
    repo_path = find_repo(a.repo)
    if not repo_path:
        print(tr("unofficial-watchfaces nicht gefunden. Bitte wfmix in den Repo-Ordner legen "
                 "oder --repo /pfad/zu/unofficial-watchfaces angeben.",
                 "unofficial-watchfaces not found. Put wfmix into the repo folder "
                 "or use --repo /path/to/unofficial-watchfaces."))
        sys.exit(1)
    repo = core.Repo(repo_path, a.work)
    os.makedirs(repo.workdir, exist_ok=True)
    cfg = {"repo": repo_path, "host": a.host, "port": a.port,
           "wallpaper": os.path.abspath(a.wall) if a.wall else ""}
    runner = Runner(cfg)

    if a.command != "ui":
        CONSOLE = True
    if a.command == "list":
        for f in repo.faces():
            print("%-45s Qt%s %3d %s%s" % (f.fid, f.qt, len(f.blocks), tr("Bausteine", "blocks"),
                                           tr("  (eigenes)", "  (own)") if getattr(f, "own", False) else ""))
        return
    if a.command == "blocks":
        f = repo.face(a.arg or "")
        if not f:
            sys.exit(tr("Watchface nicht gefunden", "Watchface not found"))
        for b in f.blocks:
            j = b.to_json(False)
            print("%-28s %-16s %4d%s%s%s" % (b.key, b.type, b.lines,
                                           tr("  Helfer", "  helper") if b.helper else "",
                                           ("  " + tr("nur Qt", "Qt") + b.only + tr("", " only")) if b.only else "",
                                           ("  " + tr("braucht: ", "needs: ") + ", ".join(j["deps"])) if j["deps"] else ""))
        return
    if a.command == "build":
        with open(a.arg, encoding="utf-8") as fp:
            proj = json.load(fp)
        if a.target:
            proj["target"] = a.target
        res = core.build(repo, proj)
        print("\n".join(res["log"]))
        return
    if a.command == "test":
        d = face_dir_for(repo, a.arg or "")
        if not d:
            sys.exit(tr("nicht gefunden", "not found"))
        name = os.path.basename(d)
        qt = a.target or own_target(repo, name, repo.qt)
        runner.test(d, name, qt, wait=True)
        return
    if a.command == "deploy":
        d = face_dir_for(repo, a.arg or "")
        if not d:
            sys.exit(tr("nicht gefunden", "not found"))
        sys.exit(0 if deploy(cfg, d, os.path.basename(d)) else 1)

    srv = ThreadingHTTPServer(("127.0.0.1", a.ui_port), make_handler(repo, cfg, runner))
    url = "http://127.0.0.1:%d/" % a.ui_port
    print(tr("wfmix %s läuft: %s   (Beenden mit Strg+C)", "wfmix %s running: %s   (quit with Ctrl+C)")
          % (VERSION, url))
    print(tr("Repo: %s (Qt%s)\nEigene Watchfaces: %s", "Repo: %s (Qt%s)\nOwn watchfaces: %s")
          % (repo_path, repo.qt, repo.workdir))
    LOG.add(tr("wfmix %s bereit – Repo: %s (Qt%s)", "wfmix %s ready – repo: %s (Qt%s)")
            % (VERSION, repo_path, repo.qt), "head")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()


if __name__ == "__main__":
    main()
