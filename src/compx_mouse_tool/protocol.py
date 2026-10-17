"""Compx "report 8" configuration protocol (reference firmware).

Every command is a 16-byte output report with report id 0x08:

    [cmd, status, addr_hi, addr_lo, length, data[0..9], checksum]

checksum = 0x55 - (0x08 + sum(bytes 0..14)) & 0xff. The mouse answers with an
input report 0x08 that echoes the command byte; byte 1 is 0 on success.

Settings live in a small flash area. Each value is stored next to its own
checksum so that value + checksum == 0x55 (two-byte fields) or
sum(record) == 0x55 (four-byte DPI and key records).

Everything in this module was verified on a Cherry Xtrfy M64 Wireless
(firmware 2.00) by reading the flash, changing one setting with the mouse's
own buttons and diffing the result. Command numbers and the flash layout match
the shared Compx reference firmware documented by the OpenMouse project.
"""

from __future__ import annotations

from dataclasses import dataclass, field

REPORT_ID = 0x08
PACKET_LENGTH = 16
MAX_CHUNK = 10
FLASH_SIZE = 200


class Command:
    DEVICE_ONLINE = 0x03
    BATTERY = 0x04
    WRITE_FLASH = 0x07
    READ_FLASH = 0x08
    CURRENT_PROFILE = 0x0E
    VERSION = 0x12


#: Commands this tool is ever allowed to send. Pairing (0x05), factory reset
#: (0x09), firmware upgrade mode (0x0d), profile switching (0x0f) and dongle
#: unpairing (0x13) exist in the firmware and are deliberately never sent.
ALLOWED_COMMANDS = frozenset(
    {
        Command.DEVICE_ONLINE,
        Command.BATTERY,
        Command.WRITE_FLASH,
        Command.READ_FLASH,
        Command.CURRENT_PROFILE,
        Command.VERSION,
    }
)


class Flash:
    POLLING = 0
    DPI_STAGE_COUNT = 2
    DPI_ACTIVE = 4
    LIFT_OFF = 10
    DPI_TABLE = 12
    DPI_COLORS = 44
    KEY_TABLE = 96
    DEBOUNCE = 169
    MOTION_SYNC = 171


DPI_STAGES = 8
DPI_MIN, DPI_MAX, DPI_STEP = 50, 26000, 50
BUTTON_COUNT = 6

POLLING_RATES = {125: 0x08, 250: 0x04, 500: 0x02, 1000: 0x01}
DEBOUNCE_MS = (2, 4, 8, 12)
LIFT_OFF_MM = {1: 0x01, 2: 0x02}

BUTTON_NAMES = ("left", "right", "middle", "back", "forward", "dpi")


class KeyClass:
    DISABLED = 0
    MOUSE = 1
    DPI = 2


#: action id -> (class, 16-bit parameter)
BUTTON_ACTIONS: dict[str, tuple[int, int]] = {
    "left_click": (KeyClass.MOUSE, 0x0100),
    "right_click": (KeyClass.MOUSE, 0x0200),
    "middle_click": (KeyClass.MOUSE, 0x0400),
    "back": (KeyClass.MOUSE, 0x0800),
    "forward": (KeyClass.MOUSE, 0x1000),
    "dpi_cycle": (KeyClass.DPI, 0x0100),
    "dpi_up": (KeyClass.DPI, 0x0200),
    "dpi_down": (KeyClass.DPI, 0x0300),
    "disabled": (KeyClass.DISABLED, 0x0000),
}

#: Writable two-byte fields: address -> allowed raw values.
WRITABLE_BYTES: dict[int, frozenset[int]] = {
    Flash.POLLING: frozenset(POLLING_RATES.values()),
    Flash.DPI_STAGE_COUNT: frozenset(range(1, DPI_STAGES + 1)),
    Flash.DPI_ACTIVE: frozenset(range(DPI_STAGES)),
    Flash.LIFT_OFF: frozenset(LIFT_OFF_MM.values()),
    Flash.DEBOUNCE: frozenset(DEBOUNCE_MS),
    Flash.MOTION_SYNC: frozenset({0, 1}),
}


class ProtocolError(Exception):
    pass


def packet_checksum(packet: bytes | bytearray) -> int:
    return (0x55 - ((REPORT_ID + sum(packet[:15])) & 0xFF)) & 0xFF


def data_checksum(data: bytes | bytearray) -> int:
    return (0x55 - (sum(data) & 0xFF)) & 0xFF


def build_packet(command: int, address: int = 0, data: bytes = b"", length: int | None = None) -> bytes:
    if command not in ALLOWED_COMMANDS:
        raise ProtocolError(f"command 0x{command:02x} is not allowed")
    if len(data) > MAX_CHUNK:
        raise ProtocolError("at most 10 data bytes per packet")
    packet = bytearray(PACKET_LENGTH)
    packet[0] = command
    packet[2] = (address >> 8) & 0xFF
    packet[3] = address & 0xFF
    packet[4] = len(data) if length is None else length
    packet[5 : 5 + len(data)] = data
    packet[15] = packet_checksum(packet)
    return bytes(packet)


def byte_field(value: int) -> bytes:
    return bytes([value, (0x55 - value) & 0xFF])


def byte_field_valid(raw: bytes) -> bool:
    return len(raw) >= 2 and (raw[0] + raw[1]) & 0xFF == 0x55


# --- DPI ---------------------------------------------------------------


def encode_dpi(dpi: int) -> bytes:
    if not DPI_MIN <= dpi <= DPI_MAX or dpi % DPI_STEP:
        raise ProtocolError(f"DPI must be {DPI_MIN}-{DPI_MAX} in steps of {DPI_STEP}")
    raw = dpi // DPI_STEP - 1
    low, high = raw & 0xFF, (raw >> 8) & 0x03
    flags = (high << 2) | (high << 6)
    return bytes([low, low, flags, data_checksum(bytes([low, low, flags]))])


def decode_dpi(record: bytes) -> int | None:
    if len(record) < 4 or record[0] != record[1] or sum(record[:4]) & 0xFF != 0x55:
        return None
    return ((((record[2] >> 2) & 0x03) << 8) + record[0] + 1) * DPI_STEP


# --- Buttons -----------------------------------------------------------


def encode_key(action: str) -> bytes:
    if action not in BUTTON_ACTIONS:
        raise ProtocolError(f"unknown button action {action!r}")
    cls, param = BUTTON_ACTIONS[action]
    body = bytes([cls, (param >> 8) & 0xFF, param & 0xFF])
    return body + bytes([data_checksum(body)])


def decode_key(record: bytes) -> str | None:
    if len(record) < 4 or sum(record[:4]) & 0xFF != 0x55:
        return None
    key = (record[0], (record[1] << 8) | record[2])
    for action, value in BUTTON_ACTIONS.items():
        if value == key:
            return action
    return None


# --- Parsed state ------------------------------------------------------


@dataclass
class MouseState:
    flash: bytes
    firmware: str = "?"
    battery_percent: int | None = None
    charging: bool | None = None
    wired: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def polling_hz(self) -> int | None:
        raw = self.flash[Flash.POLLING]
        return next((hz for hz, v in POLLING_RATES.items() if v == raw), None)

    @property
    def dpi_stage_count(self) -> int:
        return self.flash[Flash.DPI_STAGE_COUNT]

    @property
    def dpi_active(self) -> int:
        return self.flash[Flash.DPI_ACTIVE]

    @property
    def dpi_stages(self) -> list[int | None]:
        base = Flash.DPI_TABLE
        return [decode_dpi(self.flash[base + 4 * i : base + 4 * i + 4]) for i in range(DPI_STAGES)]

    @property
    def dpi_colors(self) -> list[tuple[int, int, int]]:
        """LED colour shown for each DPI stage (read-only in this version)."""
        base = Flash.DPI_COLORS
        return [tuple(self.flash[base + 4 * i : base + 4 * i + 3]) for i in range(DPI_STAGES)]  # type: ignore[misc]

    @property
    def lift_off_mm(self) -> int | None:
        raw = self.flash[Flash.LIFT_OFF]
        return next((mm for mm, v in LIFT_OFF_MM.items() if v == raw), None)

    @property
    def debounce_ms(self) -> int:
        return self.flash[Flash.DEBOUNCE]

    @property
    def motion_sync(self) -> bool:
        return self.flash[Flash.MOTION_SYNC] == 1

    @property
    def buttons(self) -> list[str | None]:
        base = Flash.KEY_TABLE
        return [decode_key(self.flash[base + 4 * i : base + 4 * i + 4]) for i in range(BUTTON_COUNT)]


def validate_layout(flash: bytes, firmware: str) -> list[str]:
    """Return reasons why this flash should not be written to (empty = fine)."""
    problems = []
    if len(flash) < FLASH_SIZE:
        problems.append("flash dump is incomplete")
        return problems
    if not firmware.startswith("2."):
        problems.append(f"untested firmware {firmware} (tested: 2.00)")
    for address in WRITABLE_BYTES:
        if not byte_field_valid(flash[address : address + 2]):
            problems.append(f"field at {address} has a bad checksum")
    if not 1 <= flash[Flash.DPI_STAGE_COUNT] <= DPI_STAGES:
        problems.append("unexpected DPI stage count")
    state = MouseState(flash)
    if None in state.dpi_stages:
        problems.append("DPI table does not decode")
    return problems


def validate_buttons(actions: list[str]) -> None:
    if len(actions) != BUTTON_COUNT or any(a not in BUTTON_ACTIONS for a in actions):
        raise ProtocolError("invalid button table")
    if "left_click" not in actions:
        raise ProtocolError("at least one button must stay a left click")
