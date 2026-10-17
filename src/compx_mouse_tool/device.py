"""hidraw transport and safe read/write operations."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import select
import time
from dataclasses import dataclass
from pathlib import Path

from . import protocol as p

VENDOR_ID = 0x046A
PRODUCTS = {0x0332: "wired", 0x0330: "receiver"}  # wired is preferred
CONFIG_INTERFACE = 1
TIMEOUT = 1.2

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "compx-mouse-tool"


class DeviceError(Exception):
    pass


class MouseAsleep(DeviceError):
    pass


class MouseDisconnected(DeviceError):
    pass


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    product_id: int

    @property
    def wired(self) -> bool:
        return PRODUCTS.get(self.product_id) == "wired"


def find_devices(sysfs: str = "/sys/class/hidraw") -> list[DeviceInfo]:
    found = []
    try:
        names = sorted(os.listdir(sysfs))
    except FileNotFoundError:
        return []
    for name in names:
        real = os.path.realpath(os.path.join(sysfs, name, "device"))
        parts = real.split("/")
        hid_id, usb_if = parts[-1], parts[-2] if len(parts) > 1 else ""
        try:
            _bus, vid, pid = hid_id.split(".")[0].split(":")
            vid_i, pid_i = int(vid, 16), int(pid, 16)
        except ValueError:
            continue
        if vid_i == VENDOR_ID and pid_i in PRODUCTS and usb_if.endswith(f":1.{CONFIG_INTERFACE}"):
            found.append(DeviceInfo(f"/dev/{name}", pid_i))
    order = list(PRODUCTS)
    return sorted(found, key=lambda d: order.index(d.product_id))


class Mouse:
    """One open configuration channel. Not thread-safe; use from one thread."""

    def __init__(self, info: DeviceInfo):
        self.info = info
        try:
            self.fd = os.open(info.path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise DeviceError(f"no permission for {info.path} – is the udev rule installed?") from exc
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            raise DeviceError("the mouse is already in use by another compx-mouse-tool") from exc
        self._backed_up = False

    @classmethod
    def open_first(cls) -> "Mouse":
        devices = find_devices()
        if not devices:
            raise DeviceError("no supported mouse found")
        return cls(devices[0])

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "Mouse":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- transport -----------------------------------------------------

    def _drain(self) -> None:
        while select.select([self.fd], [], [], 0)[0]:
            try:
                os.read(self.fd, 64)
            except BlockingIOError:
                break

    def exchange(self, command: int, address: int = 0, data: bytes = b"", length: int | None = None) -> bytes:
        packet = p.build_packet(command, address, data, length)
        self._drain()
        try:
            os.write(self.fd, bytes([p.REPORT_ID]) + packet)
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.EIO):
                raise MouseDisconnected("mouse disconnected") from exc
            raise DeviceError(f"USB error: {exc.strerror}") from exc
        deadline = time.monotonic() + TIMEOUT
        while (remaining := deadline - time.monotonic()) > 0:
            if not select.select([self.fd], [], [], remaining)[0]:
                break
            try:
                data_in = os.read(self.fd, 64)
            except BlockingIOError:
                continue
            # Match command *and* address so a late answer to an earlier
            # request can never be taken for this one.
            if (len(data_in) > 4 and data_in[0] == p.REPORT_ID and data_in[1] == command
                    and data_in[3:5] == packet[2:4]):
                return data_in[1:]
        raise MouseAsleep("the mouse did not answer – move it or click to wake it up")

    def _checked(self, response: bytes, what: str) -> bytes:
        if response[1] != 0:
            raise DeviceError(f"{what} rejected (status {response[1]})")
        return response

    # --- reads ---------------------------------------------------------

    def read_flash(self, address: int, length: int) -> bytes:
        out = bytearray()
        for offset in range(0, length, p.MAX_CHUNK):
            count = min(p.MAX_CHUNK, length - offset)
            r = self._checked(self.exchange(p.Command.READ_FLASH, address + offset, length=count), "flash read")
            out += r[5 : 5 + count]
        return bytes(out)

    def read_firmware(self) -> str:
        r = self._checked(self.exchange(p.Command.VERSION), "version read")
        return f"{r[5]}.{r[6]:02x}"

    def read_battery(self) -> tuple[int | None, bool]:
        r = self._checked(self.exchange(p.Command.BATTERY), "battery read")
        return (r[5] if r[5] <= 100 else None), r[6] == 1

    def read_state(self) -> p.MouseState:
        flash = self.read_flash(0, p.FLASH_SIZE)
        firmware = self.read_firmware()
        battery, charging = self.read_battery()
        state = p.MouseState(flash, firmware, battery, charging, self.info.wired)
        state.problems = p.validate_layout(flash, firmware)
        return state

    # --- writes --------------------------------------------------------

    def _host_control(self, enabled: bool) -> None:
        for _ in range(20):
            r = self._checked(self._online_packet(enabled), "host control")
            if r[9] != 1:
                if enabled and r[5] != 1:
                    raise MouseAsleep("the mouse is offline – move it or click to wake it up")
                return
            time.sleep(0.01)
        raise DeviceError("the receiver stayed busy")

    def _online_packet(self, enabled: bool) -> bytes:
        # Byte 5 carries the on/off flag while the length byte stays 0, as in
        # the reference driver.
        return self.exchange(p.Command.DEVICE_ONLINE, data=bytes([1 if enabled else 0]), length=0)

    def backup(self) -> Path:
        flash = self.read_flash(0, p.FLASH_SIZE)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = STATE_DIR / time.strftime("backup-%Y%m%d-%H%M%S.json")
        path.write_text(json.dumps({"product_id": self.info.product_id, "flash": flash.hex()}, indent=1))
        return path

    def _write(self, blocks: list[tuple[int, bytes]]) -> None:
        """Write whole fields/records, one packet each, then verify all of them.

        A record must never be split across packets: the firmware checks each
        packet on its own; the reference drivers write one record per packet,
        and a split key-table write coincided with a receiver reset.
        Unchanged fields are skipped, except the active DPI stage: the mouse
        does not store stage changes made with its buttons, so the flash value
        can be stale and the write must always go out.
        """
        flash = self.read_flash(0, p.FLASH_SIZE)
        state_problems = p.validate_layout(flash, self.read_firmware())
        if state_problems:
            raise DeviceError("writing disabled: " + "; ".join(state_problems))
        blocks = [(a, d) for a, d in blocks if a == p.Flash.DPI_ACTIVE or flash[a : a + len(d)] != d]
        if not blocks:
            return
        if any(len(d) > p.MAX_CHUNK for _, d in blocks):
            raise p.ProtocolError("a field does not fit into one packet")
        if not self._backed_up:
            self.backup()
            self._backed_up = True
        self._host_control(True)
        try:
            for address, data in blocks:
                self._checked(self.exchange(p.Command.WRITE_FLASH, address, data), "flash write")
        finally:
            try:
                self._host_control(False)
            except DeviceError:
                pass
        for address, data in blocks:
            if self.read_flash(address, len(data)) != data:
                raise DeviceError(f"the mouse did not confirm the write at {address}")

    def write_byte(self, address: int, value: int) -> None:
        allowed = p.WRITABLE_BYTES.get(address)
        if allowed is None or value not in allowed:
            raise p.ProtocolError(f"value {value} is not allowed at {address}")
        self._write([(address, p.byte_field(value))])

    def set_polling(self, hz: int) -> None:
        if hz not in p.POLLING_RATES:
            raise p.ProtocolError(f"unsupported polling rate {hz}")
        self.write_byte(p.Flash.POLLING, p.POLLING_RATES[hz])

    def set_debounce(self, ms: int) -> None:
        self.write_byte(p.Flash.DEBOUNCE, ms)

    def set_lift_off(self, mm: int) -> None:
        if mm not in p.LIFT_OFF_MM:
            raise p.ProtocolError(f"unsupported lift-off distance {mm}")
        self.write_byte(p.Flash.LIFT_OFF, p.LIFT_OFF_MM[mm])

    def set_motion_sync(self, enabled: bool) -> None:
        self.write_byte(p.Flash.MOTION_SYNC, int(enabled))

    def set_dpi_stage_count(self, count: int) -> None:
        if not 1 <= count <= p.DPI_STAGES:
            raise p.ProtocolError(f"stage count must be 1-{p.DPI_STAGES}")
        active = self.read_flash(p.Flash.DPI_ACTIVE, 2)[0]
        if active >= count:  # keep the active stage inside the new range
            self.write_byte(p.Flash.DPI_ACTIVE, count - 1)
        self.write_byte(p.Flash.DPI_STAGE_COUNT, count)

    def set_active_stage(self, index: int) -> None:
        count = self.read_flash(p.Flash.DPI_STAGE_COUNT, 2)[0]
        if not 0 <= index < count:
            raise p.ProtocolError(f"stage {index + 1} is not active (only {count} stages)")
        self.write_byte(p.Flash.DPI_ACTIVE, index)

    def set_dpi_stages(self, stages: list[int]) -> None:
        if len(stages) != p.DPI_STAGES:
            raise p.ProtocolError(f"expected {p.DPI_STAGES} DPI values")
        self._write([(p.Flash.DPI_TABLE + 4 * i, p.encode_dpi(d)) for i, d in enumerate(stages)])

    def set_buttons(self, actions: list[str]) -> None:
        p.validate_buttons(actions)
        self._write([(p.Flash.KEY_TABLE + 4 * i, p.encode_key(a)) for i, a in enumerate(actions)])
