"""Protocol tests against flash dumps captured from a real M64 (firmware 2.00)."""

import json
import unittest
from pathlib import Path

from compx_mouse_tool import protocol as p
from compx_mouse_tool.device import DeviceError, DeviceInfo, Mouse

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> bytes:
    return bytes.fromhex(json.loads((FIXTURES / name).read_text())["flash"])


class FactoryDump(unittest.TestCase):
    def setUp(self):
        self.state = p.MouseState(load("dump1.json"), firmware="2.00")

    def test_settings(self):
        s = self.state
        self.assertEqual(s.polling_hz, 1000)
        self.assertEqual(s.debounce_ms, 4)
        self.assertEqual(s.lift_off_mm, 1)
        self.assertTrue(s.motion_sync)
        self.assertEqual(s.dpi_stage_count, 8)
        self.assertEqual(s.dpi_active, 1)

    def test_dpi_table_matches_manual(self):
        self.assertEqual(self.state.dpi_stages, [400, 800, 1200, 1600, 3200, 4000, 7200, 26000])

    def test_buttons(self):
        self.assertEqual(self.state.buttons, ["left_click", "right_click", "middle_click", "back", "forward", "dpi_cycle"])

    def test_layout_ok(self):
        self.assertEqual(p.validate_layout(self.state.flash, "2.00"), [])

    def test_unknown_firmware_blocks_writes(self):
        self.assertTrue(p.validate_layout(self.state.flash, "3.01"))


class ButtonDiff(unittest.TestCase):
    """dump2 = dump1 after switching to 125 Hz and 8 ms on the mouse itself."""

    def test_only_expected_bytes_changed(self):
        a, b = load("dump1.json"), load("dump2.json")
        changed = [i for i in range(len(a)) if a[i] != b[i]]
        self.assertEqual(changed, [0, 1, 169, 170])
        state = p.MouseState(b)
        self.assertEqual((state.polling_hz, state.debounce_ms), (125, 8))


class Encoding(unittest.TestCase):
    def test_dpi_roundtrip(self):
        for dpi in range(p.DPI_MIN, p.DPI_MAX + 1, p.DPI_STEP):
            self.assertEqual(p.decode_dpi(p.encode_dpi(dpi)), dpi)

    def test_dpi_bytes_match_flash(self):
        flash = load("dump1.json")
        self.assertEqual(p.encode_dpi(26000), flash[40:44])
        self.assertEqual(p.encode_dpi(400), flash[12:16])

    def test_dpi_rejects_bad_values(self):
        for dpi in (0, 25, 26050, 401):
            with self.assertRaises(p.ProtocolError):
                p.encode_dpi(dpi)

    def test_key_bytes_match_flash(self):
        flash = load("dump1.json")
        expected = b"".join(p.encode_key(a) for a in ("left_click", "right_click", "middle_click", "back", "forward", "dpi_cycle"))
        self.assertEqual(expected, flash[96:120])

    def test_packets_match_verified_writes(self):
        # Sent to the real mouse and confirmed by read-back.
        self.assertEqual(p.build_packet(0x07, 0, p.byte_field(1)).hex(" "),
                         "07 00 00 00 02 01 54 00 00 00 00 00 00 00 00 ef")
        self.assertEqual(p.build_packet(0x07, 10, p.byte_field(1)).hex(" "),
                         "07 00 00 0a 02 01 54 00 00 00 00 00 00 00 00 e5")

    def test_dangerous_commands_refused(self):
        for command in (0x05, 0x09, 0x0D, 0x0F, 0x13):
            with self.assertRaises(p.ProtocolError):
                p.build_packet(command)

    def test_left_click_guard(self):
        with self.assertRaises(p.ProtocolError):
            p.validate_buttons(["right_click"] * 6)
        p.validate_buttons(["right_click", "left_click", "middle_click", "back", "forward", "dpi_cycle"])


class FakeMouse(Mouse):
    """Mouse with an in-memory flash instead of hidraw."""

    def __init__(self, flash: bytes):
        self.info = DeviceInfo("/dev/null", 0x0330)
        self.fd = -1
        self.flash = bytearray(flash)
        self._backed_up = True
        self.sent = []

    def exchange(self, command, address=0, data=b"", length=None):
        packet = p.build_packet(command, address, data, length)
        self.sent.append(packet)
        r = bytearray(packet)
        if command == p.Command.READ_FLASH:
            r[5 : 5 + packet[4]] = self.flash[address : address + packet[4]]
        elif command == p.Command.WRITE_FLASH:
            self.flash[address : address + len(data)] = data
        elif command == p.Command.VERSION:
            r[5], r[6] = 2, 0
        elif command == p.Command.DEVICE_ONLINE:
            r[5], r[9] = 1, 0
        return bytes(r)


class Writes(unittest.TestCase):
    def setUp(self):
        self.mouse = FakeMouse(load("dump1.json"))

    def test_polling_write_is_verified(self):
        self.mouse.set_polling(500)
        self.assertEqual(p.MouseState(bytes(self.mouse.flash)).polling_hz, 500)

    def test_writes_wrapped_in_host_control(self):
        self.mouse.set_motion_sync(False)
        commands = [pkt[0] for pkt in self.mouse.sent]
        write = commands.index(p.Command.WRITE_FLASH)
        self.assertEqual(commands[write - 1], p.Command.DEVICE_ONLINE)
        self.assertEqual(commands[write + 1], p.Command.DEVICE_ONLINE)

    def test_dpi_table_write(self):
        stages = [800, 1600, 2400, 3200, 6400, 12800, 20000, 26000]
        self.mouse.set_dpi_stages(stages)
        self.assertEqual(p.MouseState(bytes(self.mouse.flash)).dpi_stages, stages)

    def test_out_of_range_values_refused(self):
        with self.assertRaises(p.ProtocolError):
            self.mouse.write_byte(p.Flash.DEBOUNCE, 3)
        with self.assertRaises(p.ProtocolError):
            self.mouse.write_byte(50, 1)  # not a known field

    def test_corrupt_flash_blocks_writes(self):
        self.mouse.flash[p.Flash.POLLING + 1] ^= 0xFF
        with self.assertRaises(DeviceError):
            self.mouse.set_polling(1000)

    def test_failed_readback_raises(self):
        class Stubborn(FakeMouse):
            def exchange(self, command, address=0, data=b"", length=None):
                if command == p.Command.WRITE_FLASH:
                    return p.build_packet(command, address, data, length)
                return super().exchange(command, address, data, length)

        with self.assertRaises(DeviceError):
            Stubborn(load("dump1.json")).set_polling(125)


if __name__ == "__main__":
    unittest.main()
