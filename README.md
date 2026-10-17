# compx-mouse-tool

Unofficial Linux tool for the **Cherry Xtrfy M64 Wireless**. Cherry ships no
software for this mouse; this tool talks to its configuration interface
directly. Not affiliated with Cherry or Xtrfy – use at your own risk.

## Features

- Window, system tray icon (battery, low-battery warning, DPI switch) and CLI
- DPI stages (values, count, active stage), polling rate, debounce,
  lift-off distance, motion sync
- Button mapping for all 6 buttons
- Works over cable (`046a:0332`) and 2.4 GHz receiver (`046a:0330`),
  tested with firmware 2.00
- English and German UI

## Install

```sh
sudo cp udev/70-compx-mouse-tool.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw

pipx install '.[gui]'                    # or, with distro PySide6:
pipx install --system-site-packages .    # (Fedora: dnf install python3-pyside6)
cp data/compx-mouse-tool.desktop ~/.local/share/applications/   # menu entry
```

## Use

```sh
compx-mouse-tool-gui [--tray]
compx-mouse-tool status
compx-mouse-tool set --polling 1000 --debounce 4 --lift-off 1 --motion-sync on
compx-mouse-tool set --stages 5 --active-stage 2 --button forward=dpi_up
```

Asleep wireless mouse? Move it once. The mouse does not report DPI changes
made with its own buttons, so the app shows the *stored* stage.

## Safety

Only read/write/status commands are ever sent – pairing, factory reset and
firmware mode are blocked in code. Every write is limited to known fields,
checked by read-back, and the settings are backed up to
`~/.local/state/compx-mouse-tool/` first. Factory reset on the mouse: hold both
side buttons + right click.

## Status

| Verified on the device | Not yet verified |
|---|---|
| reading, polling, debounce, lift-off, motion sync, buttons, active stage, stage count | writing DPI values |

Protocol details: [PROTOCOL.md](PROTOCOL.md) (based on the Compx reference
firmware as documented by [OpenMouse](https://github.com/OpenMouse-Project/mouse-protocol);
no code taken). Tests: `PYTHONPATH=src python3 -m unittest discover -s tests`.

License: GPL-3.0-or-later
