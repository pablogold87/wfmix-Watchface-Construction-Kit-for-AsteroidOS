# wfmix – Watchface-Baukasten für AsteroidOS (Qt5)

🇬🇧 [English instructions: README.md](README.md)

Mit wfmix kombinierst du Bausteine (Zeiger, Ziffern, Ringe, Datum, Akku …) aus beliebigen
Watchfaces des Repos [AsteroidOS/unofficial-watchfaces](https://github.com/AsteroidOS/unofficial-watchfaces)
zu einem neuen Watchface. Du kannst Teile löschen, den Code einzelner Bausteine bearbeiten,
das Ergebnis am PC testen und per SSH auf die Uhr laden – alles über eine kleine Oberfläche im Browser.

Gedacht für **AsteroidOS ≤ 2.1 (Qt 5.15)**, also den Repo-Stand `2c605dc` von unofficial-watchfaces.
(Der aktuelle `master` von unofficial-watchfaces ist für Qt6 / AsteroidOS 2.2.)

![Oberfläche von wfmix](docs/screenshot-ui.png)

| Testfenster am PC | Beispiel-Ergebnis |
|---|---|
| ![Testfenster](docs/screenshot-test.png) | ![Beispiel-Watchface](docs/example-watchface.png) |

## Voraussetzungen

- Linux mit Python 3.8 oder neuer (nur Standardbibliothek, keine Zusatzpakete)
- Für den Test am PC: Qt5-`qmlscene` und QML-Module (Ubuntu/Debian):
  ```
  sudo apt install qmlscene qml-module-qtquick2 qml-module-qtquick-controls2 qml-module-qtquick-layouts qml-module-qtgraphicaleffects qml-module-qt-labs-settings qml-module-qtquick-dialogs qml-module-qtquick-shapes qml-module-qtquick-window2
  ```
- Zum Übertragen: eine AsteroidOS-Uhr im Developer-Mode (Einstellungen → USB), erreichbar mit `ssh root@192.168.2.15` ohne Passwortabfrage

## Installation

```
git clone https://github.com/AsteroidOS/unofficial-watchfaces
cd unofficial-watchfaces
git checkout 2c605dc
git clone https://github.com/<dein-benutzername>/wfmix
python3 wfmix/wfmix.py
```

wfmix muss im Ordner `unofficial-watchfaces` liegen (`unofficial-watchfaces/wfmix/wfmix.py`).
Alternativ gibst du den Pfad mit `--repo /pfad/zu/unofficial-watchfaces` an.

Der Browser öffnet sich auf http://127.0.0.1:8777. Wenn du im Ordner `wfmix` stehst, startest du mit `python3 wfmix.py`.

## Bedienung

- **Links:** Watchface wählen (Suche, Filter Analog/Digital/Eigene)
- **Mitte:** die Bausteine dieses Watchfaces
  - Klick = Code ansehen, **＋** = übernehmen
  - „Alle übernehmen“ = ein Watchface komplett übernehmen, um danach Teile zu löschen
  - Markierungen: *braucht* = wird automatisch mitgenommen · *nutzt* = Wurzel-Properties, die automatisch mitkommen · *Helfer* = zeichnet nichts, steuert nur
- **Rechts:** dein Watchface
  - ✕ löscht, ↑ ↓ oder Ziehen sortiert um (weiter unten = liegt oben)
  - Klick auf einen Eintrag öffnet den Code zum Bearbeiten (Übernehmen mit Strg+S)
  - **Basis** liefert Wurzel-Einstellungen und das Vorschaubild
- **▶ Am PC testen:** erzeugt das Watchface und öffnet das Testfenster mit Rund/Eckig, Ambient-Modus, 12h, fester Uhrzeit usw. Erneut klicken = neu laden.
- **⇪ Auf die Uhr:** kopiert per `scp` nach `/usr/share/`, aktiviert das Watchface und startet die Oberfläche der Uhr neu
- **Protokoll:** zeigt, was automatisch ergänzt, umbenannt oder entfernt wurde, und die QML-Meldungen des Tests

## Was automatisch passiert

- Bausteine, deren ids ein gewählter Baustein benutzt, werden mitgenommen.
- Helfer wie `Connections { target: wallClock … }`, die gewählte Zeiger bewegen, werden mitgenommen. Zeilen für nicht gewählte Teile werden dabei herausgefiltert, ebenso in `Component.onCompleted` und in Funktionen.
- Wurzel-Properties und Funktionen werden nur mitgenommen, wenn sie gebraucht werden.
- Bei Namenskonflikten zwischen verschiedenen Watchfaces werden ids und Properties umbenannt (z.B. `hourHand_Pulsedot`).
- Importe werden zusammengeführt. Bilder, Schriften und JS-Dateien der Quellen werden mitkopiert.
- Die Lizenzköpfe aller Quell-Watchfaces bleiben in der erzeugten Datei erhalten.
- Das Projekt wird in `mixed/<name>/wfmix-projekt.json` gespeichert und ist über „Projekt laden“ wieder bearbeitbar.

## Tipp: flüssiger Sekundenzeiger

Den Baustein mit dem Sekundenzeiger bearbeiten und in dessen Rotation ein `Behavior` einfügen:

```qml
transform: Rotation {
    id: secondRot
    origin.x: parent.width / 2
    origin.y: parent.height / 2

    Behavior on angle {
        enabled: !displayAmbient
        RotationAnimation {
            duration: 1000
            easing.type: Easing.Linear
            direction: RotationAnimation.Clockwise
        }
    }
}
```

Hinweis: Die durchgehende Animation braucht bei eingeschaltetem Display spürbar mehr Akku.

## Kommandozeile

```
python3 wfmix/wfmix.py list                     # alle Watchfaces
python3 wfmix/wfmix.py blocks analog-commander  # Bausteine eines Watchfaces
python3 wfmix/wfmix.py build mixed/NAME/wfmix-projekt.json
python3 wfmix/wfmix.py test NAME
python3 wfmix/wfmix.py deploy NAME --host 192.168.2.15
python3 wfmix/wfmix.py --help                   # alle Optionen
```

## Hinweise

- Der Ordner `stubs/` enthält Platzhalter für `org.asteroid.controls`, `org.asteroid.utils` und `org.asteroid.sensorlogd`. Sie werden **nur am PC** benutzt, damit Watchfaces mit diesen Importen im Testfenster laufen. Auf die Uhr kommen sie nicht.
- Für den Test wird eine Kopie von `loader.qml` mit `compositor`, `desktop`, `localeManager` und `burnInProtectionManager` erzeugt (unter `~/.cache/wfmix`).
- Erzeugte Watchfaces enthalten Code und Dateien aus unofficial-watchfaces. Dafür gelten weiterhin deren Lizenzen (meist LGPL-2.1-or-later; Schriften laut README von unofficial-watchfaces).

## Lizenz

wfmix selbst steht unter der [MIT-Lizenz](LICENSE).
