#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wfmix - Watchface-Baukasten fuer AsteroidOS (Qt5 / AsteroidOS <= 2.1)

Startet eine lokale Oberflaeche im Browser, mit der man Bausteine
(Zeiger, Ziffern, Datum, Akku-Anzeige, ...) aus beliebigen Watchfaces des
unofficial-watchfaces-Repos zu einem neuen Watchface kombinieren, Bausteine
loeschen oder bearbeiten, am PC mit qmlscene testen und per SSH auf die Uhr
laden kann.

Aufruf (im Ordner unofficial-watchfaces/wfmix oder mit --repo):
    python3 wfmix.py                 # Oberflaeche starten
    python3 wfmix.py --help          # alle Optionen

Kommandozeile ohne Oberflaeche:
    python3 wfmix.py list
    python3 wfmix.py blocks analog-commander
    python3 wfmix.py build mixed/mein-face/wfmix-projekt.json
    python3 wfmix.py test  mein-face
    python3 wfmix.py deploy mein-face
"""

import argparse
import collections
import json
import mimetypes
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wfmix_core as core  # noqa: E402

VERSION = "1.0"

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
            for l in str(text).rstrip("\n").split("\n"):
                self.n += 1
                self.lines.append((self.n, kind, l))
        if CONSOLE:
            print(text)

    def since(self, k):
        with self.lock:
            return [{"n": n, "k": kind, "t": t} for n, kind, t in self.lines if n > k]


CONSOLE = False
LOG = Log()


# --------------------------------------------------------------------------
# Test am PC (qmlscene, Qt5) - wie "watchface test", aber mit Stubs fuer
# org.asteroid.* und ohne die Pfadprobleme beim Hintergrundbild
# --------------------------------------------------------------------------

# Meldungen von qmlscene, die beim Test immer kommen und nichts bedeuten
NOISE = ("XDG_RUNTIME_DIR", "does not exist.", 'Invalid URL: ""', "Implicitly defined onFoo",
         "propagateSizeHints", "Could not find the Qt platform plugin \"wayland\"")


class Runner(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.fontconf = None
        self.lock = threading.Lock()

    def qmlscene(self):
        for b in ("qmlscene", "qmlscene-qt5"):
            p = shutil.which(b)
            if p:
                return p
        cand = "/usr/lib/qt5/bin/qmlscene"
        if os.path.exists(cand):
            return cand
        cand = "/usr/lib/x86_64-linux-gnu/qt5/bin/qmlscene"
        return cand if os.path.exists(cand) else None

    def loader(self):
        """Kopie von loader.qml mit zusaetzlichen Objekten, die der Launcher
        auf der Uhr bereitstellt (compositor, desktop, localeManager, ...)."""
        src = os.path.join(self.cfg["repo"], "loader.qml")
        with open(src, encoding="utf-8") as fp:
            t = fp.read()
        extra = """
            QtObject {
                id: compositor
                property bool amb: appRoot.displayAmbient
                signal displayAmbientEntered()
                signal displayAmbientLeft()
                signal displayAmbientChanged()
                onAmbChanged: { displayAmbientChanged(); if (amb) displayAmbientEntered(); else displayAmbientLeft() }
            }
            QtObject {
                id: desktop
                property bool displayAmbient: appRoot.displayAmbient
            }
            QtObject {
                id: localeManager
                property string changesObserver: ""
            }
            QtObject {
                id: burnInProtectionManager
                property real widthOffset: 0
                property real heightOffset: 0
            }
"""
        marker = "            Item {\n                id: use12H"
        if marker in t and "id: compositor" not in t:
            t = t.replace(marker, extra + marker, 1)
        d = os.path.join(os.path.expanduser("~"), ".cache", "wfmix")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "loader.qml")
        with open(p, "w", encoding="utf-8") as fp:
            fp.write(t)
        return p

    def stop(self):
        with self.lock:
            p = self.proc
            self.proc = None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self._cleanup_font()

    def _cleanup_font(self):
        if self.fontconf and os.path.exists(self.fontconf):
            try:
                os.remove(self.fontconf)
            except OSError:
                pass
        self.fontconf = None

    def test(self, face_dir, name, wait=False, timeout=None):
        qs = self.qmlscene()
        if not qs:
            LOG.add("qmlscene (Qt5) nicht gefunden. Installieren mit:\n"
                    "  sudo apt install qmlscene qml-module-qtquick2 qml-module-qtquick-controls2 "
                    "qml-module-qtquick-layouts qml-module-qtgraphicaleffects qml-module-qt-labs-settings "
                    "qml-module-qtquick-dialogs qml-module-qtquick-shapes qml-module-qtquick-window2",
                    "err")
            return None
        self.stop()
        repo = self.cfg["repo"]
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
        wall_r = self.cfg.get("wallpaper_round") or wall
        wfdir = os.path.join(face_dir, core.WF_SUBDIR) + "/"
        cmd = [qs, name, wall, wall_r, wfdir, self.loader(),
               "-I", os.path.join(repo, "fake-components"), "-I", os.path.join(HERE, "stubs")]
        LOG.add("▶ Teste %s am PC (qmlscene) …" % name, "head")
        env = dict(os.environ)
        env.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
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
                # Dateipfade kuerzen
                line = line.replace("file://" + wfdir, "")
                out.append(line)
                kind = "warn"
                if any(k in line for k in ("is not a type", "Error", "error", "not installed",
                                           "Cannot assign", "not defined", "set multiple times",
                                           "Expected token", "Unexpected")):
                    kind = "err"
                LOG.add("   " + line, kind)
            rc = p.wait()
            LOG.add("■ Testfenster geschlossen (Code %s)" % rc, "head")
            if self.proc is p:
                self._cleanup_font()

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        if wait:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGTERM)
            t.join(5)
            self._cleanup_font()
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
        LOG.add("  Zeitueberschreitung", "err")
        return False
    for l in p.stdout.splitlines():
        if "Pseudo-terminal" in l or not l.strip():
            continue
        LOG.add("    " + l, "warn" if p.returncode == 0 else "err")
    if p.returncode != 0:
        LOG.add("  Fehler (Code %d)" % p.returncode, "err")
    return p.returncode == 0


def ssh(cfg, user, command):
    cmd = ["ssh"] + _ssh_base(cfg) + ["%s@%s" % (user, cfg["host"]), command]
    return run_cmd(cmd, "ssh %s@%s %s" % (user, cfg["host"], command))


def deploy(cfg, face_dir, name, restart=True, activate=True):
    host = cfg["host"]
    share = os.path.join(face_dir, "usr", "share")
    if not os.path.isdir(share):
        LOG.add("Watchface %s ist noch nicht erzeugt." % name, "err")
        return False
    LOG.add("▶ Lade %s auf die Uhr (%s) …" % (name, host), "head")
    entries = [os.path.join(share, e) for e in sorted(os.listdir(share))]
    cmd = ["scp", "-r", "-P", str(cfg["port"]), "-o", "ConnectTimeout=6", "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=accept-new"] + entries + ["root@%s:/usr/share/" % host]
    if not run_cmd(cmd, "scp -r … root@%s:/usr/share/" % host):
        LOG.add("Kopieren fehlgeschlagen. Ist die Uhr per USB verbunden und unter "
                "Einstellungen → USB der Developer-Modus aktiv? Klappt 'ssh root@%s' im Terminal?" % host,
                "err")
        return False
    if activate:
        ssh(cfg, "ceres", "dconf write /desktop/asteroid/watchface "
            "\"'file:///usr/share/asteroid-launcher/watchfaces/%s.qml'\"" % name)
    if restart:
        LOG.add("  Starte die Oberflaeche der Uhr neu (neue Schriften/Bilder werden geladen) …")
        ssh(cfg, "root", "systemctl restart user@1000")
    LOG.add("■ Fertig – %s ist auf der Uhr%s." % (name, " und aktiv" if activate else ""), "ok")
    return True


def undeploy(cfg, name):
    LOG.add("▶ Entferne %s von der Uhr …" % name, "head")
    ok = ssh(cfg, "root", "rm -f /usr/share/asteroid-launcher/watchfaces/%s.qml "
             "/usr/share/asteroid-launcher/watchfaces-preview/*/%s.png" % (name, name))
    if ok:
        LOG.add("■ Entfernt. Bilder und Schriften bleiben liegen (koennen von anderen Faces genutzt werden).",
                "ok")
    return ok


def watch_info(cfg):
    LOG.add("▶ Pruefe Verbindung zur Uhr …", "head")
    return ssh(cfg, "root", "grep PRETTY_NAME /etc/os-release; ls /usr/lib | grep -m1 libQt5Core")


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


def make_handler(repo, cfg, runner):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            try:
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Browser hat die Anfrage abgebrochen

        def _file(self, path):
            if not path or not os.path.isfile(path):
                return self._send(404, {"error": "nicht gefunden"})
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
                        faces.append(d)
                    return self._send(200, {
                        "version": VERSION, "repo": repo.path, "work": repo.workdir,
                        "host": cfg["host"], "qmlscene": bool(runner.qmlscene()),
                        "ssh": bool(shutil.which("ssh")), "faces": faces,
                        "projects": core.list_projects(repo), "errors": repo.errors,
                    })
                if u.path == "/api/face":
                    f = repo.face(q.get("id", ""))
                    if not f:
                        return self._send(404, {"error": "Watchface nicht gefunden"})
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
                    return self._send(200 if p else 404, p or {"error": "Projekt nicht gefunden"})
                if u.path == "/api/log":
                    return self._send(200, LOG.since(int(q.get("since", "0") or 0)))
                if u.path == "/api/source":
                    f = repo.face(q.get("id", ""))
                    return self._send(200, f.text if f else "", "text/plain; charset=utf-8")
                return self._send(404, {"error": "unbekannt"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa
                return self._send(500, {"error": str(e)})

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            try:
                if u.path == "/api/build" or u.path == "/api/preview":
                    write = u.path == "/api/build"
                    res = core.build(repo, data.get("project") or {}, write=write)
                    if write:
                        LOG.add("▶ Watchface '%s' erzeugt" % res["name"], "head")
                        for l in res["log"]:
                            LOG.add("  " + l, "info")
                    return self._send(200, res)
                if u.path == "/api/test":
                    proj = data.get("project")
                    if proj:
                        res = core.build(repo, proj, write=True)
                        for l in res["log"]:
                            LOG.add("  " + l, "info")
                        name = res["name"]
                    else:
                        name = data.get("name")
                    d = face_dir_for(repo, data.get("dir") or name)
                    if not d:
                        return self._send(404, {"error": "nicht gefunden"})
                    runner.test(d, name)
                    return self._send(200, {"ok": True, "name": name})
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

                    def bg():
                        deploy(cfg, d, name, restart=data.get("restart", True))
                    threading.Thread(target=bg, daemon=True).start()
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
                        LOG.add("Geoeffnet: " + p)
                    return self._send(200, {"ok": True, "path": p})
                return self._send(404, {"error": "unbekannt"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except ValueError as e:
                LOG.add("✖ " + str(e), "err")
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa
                import traceback
                LOG.add("✖ Interner Fehler: %s" % e, "err")
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


def main():
    global CONSOLE
    ap = argparse.ArgumentParser(description="Watchface-Baukasten fuer AsteroidOS (Qt5)")
    ap.add_argument("command", nargs="?", default="ui",
                    help="ui (Standard), list, blocks FACE, build PROJEKT.json, test NAME, deploy NAME")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--repo", help="Pfad zu unofficial-watchfaces (Standard: automatisch)")
    ap.add_argument("--work", help="Ordner fuer eigene Watchfaces (Standard: <repo>/mixed)")
    ap.add_argument("--host", default="192.168.2.15", help="IP der Uhr (Standard 192.168.2.15)")
    ap.add_argument("--port", type=int, default=22, help="SSH-Port der Uhr")
    ap.add_argument("--ui-port", type=int, default=8777, help="Port der Oberflaeche")
    ap.add_argument("--wall", help="Hintergrundbild fuer den PC-Test")
    ap.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch oeffnen")
    a = ap.parse_args()

    repo_path = find_repo(a.repo)
    if not repo_path:
        print("unofficial-watchfaces nicht gefunden. Bitte wfmix in den Repo-Ordner legen "
              "oder --repo /pfad/zu/unofficial-watchfaces angeben.")
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
            print("%-45s %3d Bausteine%s" % (f.fid, len(f.blocks), "  (eigenes)" if getattr(f, "own", False) else ""))
        return
    if a.command == "blocks":
        f = repo.face(a.arg or "")
        if not f:
            sys.exit("Watchface nicht gefunden")
        for b in f.blocks:
            j = b.to_json(False)
            print("%-28s %-16s %4d Z.%s%s" % (b.key, b.type, b.lines,
                                             "  Helfer" if b.helper else "",
                                             ("  braucht: " + ", ".join(j["deps"])) if j["deps"] else ""))
        return
    if a.command == "build":
        with open(a.arg, encoding="utf-8") as fp:
            proj = json.load(fp)
        res = core.build(repo, proj)
        print("\n".join(res["log"]))
        return
    if a.command == "test":
        d = face_dir_for(repo, a.arg or "")
        if not d:
            sys.exit("nicht gefunden")
        runner.test(d, os.path.basename(d), wait=True)
        return
    if a.command == "deploy":
        d = face_dir_for(repo, a.arg or "")
        if not d:
            sys.exit("nicht gefunden")
        sys.exit(0 if deploy(cfg, d, os.path.basename(d)) else 1)

    srv = ThreadingHTTPServer(("127.0.0.1", a.ui_port), make_handler(repo, cfg, runner))
    url = "http://127.0.0.1:%d/" % a.ui_port
    print("wfmix %s läuft: %s   (Beenden mit Strg+C)" % (VERSION, url))
    print("Repo: %s\nEigene Watchfaces: %s" % (repo_path, repo.workdir))
    LOG.add("wfmix %s bereit – Repo: %s" % (VERSION, repo_path), "head")
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
