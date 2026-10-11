# wfmix – Watchface Construction Kit for AsteroidOS (Qt5)

🇩🇪 [Deutsche Anleitung: LIESMICH.md](LIESMICH.md)

wfmix lets you combine building blocks (hands, numerals, rings, date, battery …) from any
watchface in [AsteroidOS/unofficial-watchfaces](https://github.com/AsteroidOS/unofficial-watchfaces)
into a new watchface. You can delete parts, edit the code of individual blocks, test the result
on your PC and deploy it to the watch via SSH – all from a small web UI in your browser.

Intended for **AsteroidOS ≤ 2.1 (Qt 5.15)**, i.e. repository state `2c605dc` of unofficial-watchfaces.
(The current `master` of unofficial-watchfaces targets Qt6 / AsteroidOS 2.2.)

![wfmix user interface](docs/screenshot-ui.png)

| Test window on the PC | Example result |
|---|---|
| ![Test window](docs/screenshot-test.png) | ![Example watchface](docs/example-watchface.png) |

## Requirements

- Linux with Python 3.8 or newer (standard library only, no extra packages)
- For testing on the PC: Qt5 `qmlscene` and QML modules (Ubuntu/Debian):
  ```
  sudo apt install qmlscene qml-module-qtquick2 qml-module-qtquick-controls2 qml-module-qtquick-layouts qml-module-qtgraphicaleffects qml-module-qt-labs-settings qml-module-qtquick-dialogs qml-module-qtquick-shapes qml-module-qtquick-window2
  ```
- For deploying: an AsteroidOS watch in Developer mode (Settings → USB), reachable via `ssh root@192.168.2.15` without password prompt

## Installation

```
git clone https://github.com/AsteroidOS/unofficial-watchfaces
cd unofficial-watchfaces
git checkout 2c605dc
git clone https://github.com/<your-username>/wfmix
python3 wfmix/wfmix.py
```

wfmix must live inside the `unofficial-watchfaces` folder (`unofficial-watchfaces/wfmix/wfmix.py`).
Alternatively, point it to the repo with `--repo /path/to/unofficial-watchfaces`.

Your browser opens http://127.0.0.1:8777. When starting from inside the `wfmix` folder, use `python3 wfmix.py`.

## Usage

The user interface is in German; the English meaning of each label is given in parentheses.

- **Left:** pick a watchface (search, filter *Analog* / *Digital* / *Eigene* = your own)
- **Middle:** the building blocks (*Bausteine*) of that watchface
  - Click = view code, **＋** = add to your watchface
  - *Alle übernehmen* (add all) = take over a complete watchface so you can delete parts afterwards
  - Tags: *braucht* (needs) = added automatically · *nutzt* (uses) = root properties that come along automatically · *Helfer* (helper) = draws nothing, only controls other blocks
- **Right:** your watchface (*Mein Watchface*)
  - ✕ deletes, ↑ ↓ or drag & drop reorders (further down = drawn on top)
  - Click an entry to edit its code (*Übernehmen* or Ctrl+S to apply)
  - *Basis* (base) provides the root settings and the preview image
- **▶ Am PC testen** (test on PC): builds the watchface and opens the test window with round/square, ambient mode, 12h, fixed time, etc. Click again = reload.
- **⇪ Auf die Uhr** (deploy to watch): copies to `/usr/share/` via `scp`, activates the watchface and restarts the watch UI
- ***Protokoll*** (log): shows what was added, renamed or removed automatically, plus the QML messages from the test

## What happens automatically

- Blocks whose ids are used by a selected block are added.
- Helpers such as `Connections { target: wallClock … }` that move selected hands are added. Lines for parts that were not selected are filtered out, as are the matching lines in `Component.onCompleted` and in functions.
- Root properties and functions are only included when they are needed.
- On name conflicts between different watchfaces, ids and properties are renamed (e.g. `hourHand_Pulsedot`).
- Imports are merged. Images, fonts and JS files from the source watchfaces are copied along.
- The license headers of all source watchfaces are kept in the generated file.
- The project is saved to `mixed/<name>/wfmix-projekt.json` and can be edited again via *Projekt laden* (load project).

## Tip: smooth second hand

Edit the block containing the second hand and add a `Behavior` to its rotation:

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

Note: the continuous animation uses noticeably more battery while the display is on.

## Command line

```
python3 wfmix/wfmix.py list                     # all watchfaces
python3 wfmix/wfmix.py blocks analog-commander  # building blocks of a watchface
python3 wfmix/wfmix.py build mixed/NAME/wfmix-projekt.json
python3 wfmix/wfmix.py test NAME
python3 wfmix/wfmix.py deploy NAME --host 192.168.2.15
python3 wfmix/wfmix.py --help                   # all options
```

## Notes

- The `stubs/` folder contains placeholders for `org.asteroid.controls`, `org.asteroid.utils` and `org.asteroid.sensorlogd`. They are used **on the PC only**, so watchfaces with these imports run in the test window. They are never copied to the watch.
- For testing, a copy of `loader.qml` with `compositor`, `desktop`, `localeManager` and `burnInProtectionManager` is created (in `~/.cache/wfmix`).
- Generated watchfaces contain code and assets from unofficial-watchfaces. Those keep their original licenses (mostly LGPL-2.1-or-later; fonts as listed in the unofficial-watchfaces README).

## License

wfmix itself is released under the [MIT License](LICENSE).
