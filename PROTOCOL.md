# Cherry Xtrfy M64 Wireless – configuration protocol

Verified on firmware 2.00 (cable `046a:0332`, receiver `046a:0330`).

## Transport

USB interface 1 contains a vendor collection (usage page `0xFF02`) with
report id `0x08`: 16 bytes output, 16 bytes input. On Linux this is the hidraw
node of interface `:1.1`. Write `[0x08] + 16 bytes`, read input reports and
pick the one with report id 8 whose first byte echoes the command.

```
byte 0     command
byte 1     status (0 = OK in answers)
byte 2..3  flash address, big endian
byte 4     length (max. 10)
byte 5..14 data
byte 15    checksum = 0x55 - (0x08 + sum(byte 0..14))   (mod 256)
```

Other collections on the same interface: `0xFF05` input 0x10 (7 bytes),
`0xFF03` input 0x02 (7 bytes), consumer control (0x05), system control (0x03),
`0xFF04` feature 0x06 (7 bytes, contents unknown).

## Commands

| Cmd | Meaning | Answer |
|---|---|---|
| `0x03` | host control on/off (byte 5 = 1/0), needed around writes | byte 5 = 1 → mouse online, byte 9 = 1 → busy |
| `0x04` | battery | byte 5 = percent, byte 6 = 1 while charging |
| `0x07` | write flash | |
| `0x08` | read flash | data in bytes 5.. |
| `0x0e` | current profile | byte 5 (0 = profile 1) |
| `0x12` | firmware version | bytes 5.6 → `2.00` |
| `0x1d` | receiver version | rejected (status 1) on the M64 |

Known but **never sent** by this tool: `0x05` pair, `0x09` factory reset,
`0x0d` firmware upgrade mode, `0x0f` set profile, `0x13` unpair.

Over the receiver, reads work without host control. When the mouse is asleep
nothing answers – move it.

## Flash layout

Two-byte fields are `value, 0x55 - value`. Four-byte records satisfy
`sum(record) & 0xff == 0x55`.

| Address | Field | Values | Verified by |
|---|---|---|---|
| 0 | polling rate | 1 = 1000, 2 = 500, 4 = 250, 8 = 125 Hz | PR switch diff, write |
| 2 | number of DPI stages | 1–8 | write, bottom button then cycles 5 stages |
| 4 | active DPI stage | 0-based; takes effect immediately, but stage changes made with the buttons are not stored here | write, felt immediately |
| 10 | lift-off distance | 1 = 1 mm, 2 = 2 mm | button combo diff, write |
| 12 | DPI stages, 8 × 4 bytes | `[raw & 0xff, same, flags, checksum]`, raw = dpi/50 − 1, flags = (raw >> 8) × 0x44 | matches manual: 400 … 26000 |
| 44 | DPI LED colours, 8 × `[r, g, b, checksum]` | | |
| 76–95 | LED mode / brightness / speed (unverified) | | |
| 96 | buttons, 6 × `[class, param_hi, param_lo, checksum]` | see below | factory table |
| 169 | debounce time | ms (2, 4, 8, 12 via buttons) | DT switch diff, write |
| 171 | motion sync | 0/1 | button combo diff, write |
| 173 | sleep time | factory 0x12, unit unknown | |
| 175 | angle snapping | 0/1 (unverified) | |
| 177 | ripple control | 0/1 (unverified) | |

Button order: left, right, middle, back, forward, DPI (bottom).

| Class | Param | Action |
|---|---|---|
| 1 | 0x0100 / 0x0200 / 0x0400 / 0x0800 / 0x1000 | left / right / middle / back / forward |
| 2 | 0x0100 / 0x0200 / 0x0300 | DPI cycle / DPI+ / DPI− |
| 0 | 0x0000 | disabled |
