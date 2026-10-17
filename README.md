# compx-mouse-tool

Unofficial Linux configuration tool for the **Cherry Xtrfy M64 Wireless**.
The mouse has no vendor software at all – everything is normally set with a
switch and button combos on the mouse. This tool talks to the mouse's
configuration interface directly and gives you a Qt window, a tray icon with
the battery level and a command line.

> Not affiliated with or endorsed by Cherry or Xtrfy. Use at your own risk.

## Features

- DPI: values of all 8 stages (50–26 000 in steps of 50), number of active
  stages, active stage
- Polling rate (125 / 250 / 500 / 1000 Hz), debounce time (2 / 4 / 8 / 12 ms),
  lift-off distance (1 / 2 mm), motion sync
- Button mapping for all 6 buttons (clicks, back/forward, DPI cycle/+/−,
  disabled)
- Battery level and charging state, firmware version
- System tray icon with battery level, low-battery warning and quick DPI switch
- Works over the USB cable and the 2.4 GHz receiver; settings are stored in
  the mouse
- English and German UI (follows the system language)

The mouse does not report DPI stage changes made with its own buttons, so the
app shows the *stored* stage, not necessarily the one in use right now.

## Supported devices

| Device | USB ID | Status |
|---|---|---|
| Cherry Xtrfy M64 Wireless, cable | `046a:0332` | tested (firmware 2.00) |
| Cherry Xtrfy M64 Wireless, 1K receiver | `046a:0330` | tested |

The M64 is built on a Compx controller with the Compx reference firmware.
Other mice on that firmware (VXE, VGN, Lamzu, …) use the same protocol, but
are not enabled because nobody has verified their flash layout yet.
Contributions with flash dumps are welcome.

The M64 **Pro** / M68 **Pro** (8K) use different USB IDs and are not supported.

## Installation

```sh
# 1. Let your user access the mouse (once, needs root)
sudo install -m 644 udev/70-compx-mouse-tool.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw

# 2. Install the tool
pipx install '.[gui]'            # or: pip install --user '.[gui]'
```

On Fedora you can use the distribution's Qt instead of the PySide6 wheel:

```sh
sudo dnf install python3-pyside6
pipx install --system-site-packages .
```

Optional menu entry: copy `data/compx-mouse-tool.desktop` to
`~/.local/share/applications/`.

## Usage

```sh
compx-mouse-tool-gui            # window + tray icon
compx-mouse-tool-gui --tray     # start hidden in the tray

compx-mouse-tool status
compx-mouse-tool set --polling 1000 --debounce 4 --lift-off 1 --motion-sync on
compx-mouse-tool set --dpi 400,800,1200,1600,3200,4000,7200,26000 --stages 5 --active-stage 2
compx-mouse-tool set --button back=dpi_down --button forward=dpi_up
compx-mouse-tool dump           # raw settings as JSON
```

If the mouse is connected wirelessly and asleep, move it once – it only
answers while awake.

## Safety

- Only six commands are ever sent: device online, battery, read flash, write
  flash, current profile, version. Pairing, factory reset, firmware-upgrade
  mode and profile switching are refused in code.
- Writes only go to known fields with whitelisted values and are verified by
  reading them back.
- Before the first write of each session the complete settings area is saved
  to `~/.local/state/compx-mouse-tool/`.
- Writing is disabled if the firmware is not 2.x or the settings area does not
  look as expected.
- The button page refuses a mapping without any left click.
- If anything goes wrong: hold **both side buttons + right click** to restore
  the factory settings.

## Protocol

See [PROTOCOL.md](PROTOCOL.md). Command numbers and the flash layout match the
Compx reference firmware as documented by the
[OpenMouse](https://github.com/OpenMouse-Project/mouse-protocol) project;
every field used here was verified on a real M64 by changing it with the
mouse's own buttons and diffing the flash. No code was taken from OpenMouse.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compx_mouse_tool status
```

The tests run against flash dumps of a real mouse in `tests/fixtures/`.

## License

GPL-3.0-or-later
