"""Command line interface: compx-mouse-tool status | set … | dump."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from . import protocol as p
from .device import DeviceError, Mouse


def print_status(state: p.MouseState) -> None:
    battery = "?" if state.battery_percent is None else f"{state.battery_percent} %"
    if state.charging:
        battery += " (charging)"
    print(f"Connection:   {'USB cable' if state.wired else '2.4 GHz receiver'}")
    print(f"Firmware:     {state.firmware}")
    print(f"Battery:      {battery}")
    print(f"Polling rate: {state.polling_hz} Hz")
    print(f"Debounce:     {state.debounce_ms} ms")
    print(f"Lift-off:     {state.lift_off_mm} mm")
    print(f"Motion sync:  {'on' if state.motion_sync else 'off'}")
    stages = []
    for i, dpi in enumerate(state.dpi_stages[: state.dpi_stage_count]):
        mark = "*" if i == state.dpi_active else " "
        stages.append(f"{mark}{dpi}")
    print(f"DPI stages:   {' '.join(stages)}")
    for name, action in zip(p.BUTTON_NAMES, state.buttons):
        print(f"Button {name + ':':9} {action or 'unknown'}")
    for problem in state.problems:
        print(f"Warning: {problem} – writing is disabled", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compx-mouse-tool", description="Configure the Cherry Xtrfy M64 (unofficial).")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show current settings")
    sub.add_parser("dump", help="print the raw settings flash as JSON")
    s = sub.add_parser("set", help="change a setting")
    s.add_argument("--polling", type=int, choices=sorted(p.POLLING_RATES))
    s.add_argument("--debounce", type=int, choices=p.DEBOUNCE_MS)
    s.add_argument("--lift-off", type=int, choices=sorted(p.LIFT_OFF_MM))
    s.add_argument("--motion-sync", choices=("on", "off"))
    s.add_argument("--stages", type=int, metavar="N", help="number of active DPI stages (1-8)")
    s.add_argument("--active-stage", type=int, metavar="N", help="active DPI stage (1-8)")
    s.add_argument("--dpi", metavar="LIST", help="eight comma-separated DPI values, e.g. 400,800,…")
    s.add_argument("--button", action="append", default=[], metavar="NAME=ACTION",
                   help=f"buttons: {', '.join(p.BUTTON_NAMES)}; actions: {', '.join(p.BUTTON_ACTIONS)}")
    args = parser.parse_args(argv)

    try:
        with Mouse.open_first() as mouse:
            if args.cmd == "dump":
                print(json.dumps({"product_id": hex(mouse.info.product_id), "firmware": mouse.read_firmware(),
                                  "flash": mouse.read_flash(0, p.FLASH_SIZE).hex()}, indent=1))
                return 0
            if args.cmd == "set":
                if args.polling:
                    mouse.set_polling(args.polling)
                if args.debounce:
                    mouse.set_debounce(args.debounce)
                if args.lift_off:
                    mouse.set_lift_off(args.lift_off)
                if args.motion_sync:
                    mouse.set_motion_sync(args.motion_sync == "on")
                if args.dpi:
                    mouse.set_dpi_stages([int(v) for v in args.dpi.split(",")])
                if args.stages:
                    mouse.set_dpi_stage_count(args.stages)
                if args.active_stage:
                    mouse.set_active_stage(args.active_stage - 1)
                if args.button:
                    actions = list(mouse.read_state().buttons)
                    for item in args.button:
                        name, _, action = item.partition("=")
                        if name not in p.BUTTON_NAMES:
                            parser.error(f"unknown button {name!r}")
                        actions[p.BUTTON_NAMES.index(name)] = action
                    mouse.set_buttons(actions)
            print_status(mouse.read_state())
    except (DeviceError, p.ProtocolError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
