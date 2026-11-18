# xiao-ftdi

Firmware + host tools for a **Seeed XIAO ESP32-C6** USB↔UART bridge with a BLE Nordic UART Service (NUS) on the side. Use it to talk to a 3.3V UART device over USB, or over Bluetooth LE from anywhere.

- `src/main.cpp` — firmware (PlatformIO / Arduino)
- `nus_client.py` — host-side BLE terminal (Python + bleak)
- `platformio.ini` — build config

## What it does

Both directions of the bridge are transparent:

```
USB Serial  <->  UART (GPIO16 TX / GPIO17 RX)
     \             /
      \           /
        BLE NUS
```

- Anything received on USB is written to the UART **and** notified over BLE.
- Anything received on the UART is written to USB **and** notified over BLE.
- Anything written to the BLE NUS write characteristic is written to USB **and** the UART.
- No pairing or bonding — characteristics are unencrypted, connect straight through.

### Pinout / serial config

| Function        | Where                                  |
| --------------- | -------------------------------------- |
| UART TX (board → device RX) | D6 = GPIO16              |
| UART RX (board ← device TX) | D7 = GPIO17              |
| Activity LED    | GPIO15, **active-low** (built-in, only user LED) |
| Baud            | 115200, 8N1, no flow control           |
| Logic levels    | 3.3V both sides                        |

LED blinks on any bridge traffic; boot flashes 3 short + 1 long to show power-on.

### BLE

- Advertised name: `BLESerial` + last 3 bytes of the MAC, e.g. `BLESerialfffe21`
- Service: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
  - Write (`6e400002-…`, host → board): `WRITE | WRITE_NR`
  - Notify (`6e400003-…`, board → host): `NOTIFY`
- Notifications are chunked at 20 bytes (safe for the default MTU); the firmware requests MTU 512.
- After a BLE disconnect, advertising restarts automatically (~500 ms).

## Firmware: build & flash

Requires [PlatformIO](https://platformio.org/) (`pipx install platformio` is fine). The first build downloads the platform and toolchain — needs network.

```sh
pio run                 # build
pio run -t upload       # flash over USB-C
pio device monitor      # serial monitor @ 115200
```

Notes:

- `platformio.ini` pins the **pioarduino** fork of `platform-espressif32` via a GitHub release-zip URL, not the registry `platform = espressif32`. That's what gives working Arduino-ESP32 support for the C6 — don't swap it casually.
- `ARDUINO_USB_MODE=1` / `ARDUINO_USB_CDC_ON_BOOT=1` are required so `Serial` talks over the board's USB-C port (no USB↔serial chip on this board).
- `Serial1.setRxBufferSize(4096)` is called **before** `Serial1.begin()` — it is a no-op after the driver is installed.

## Host client: nus_client.py

Requires Python 3 and bleak (`pipx install bleak`).

```sh
python3 nus_client.py                    # scan, pick a device, interactive REPL
python3 nus_client.py --scan-only        # list NUS devices and exit
python3 nus_client.py -a 54:32:04:21:83:AA
python3 nus_client.py -n BLESerialfffe21 # match advertised name
```

In the REPL: type and press enter to send; `/hex 41 42 0a` sends raw bytes; `/help` lists commands; `/quit` disconnects.

Behavior worth knowing:

- Status messages go to **stderr**; stdout is a raw byte stream — pipe it, redirect it, diff it.
- Notifications are written verbatim with no added newlines: a 20-byte chunk can split a line or a UTF-8 sequence mid-way.
- Writes use `WRITE_NR` (`response=False`), matching the firmware characteristic.
- The bridge has no backlog across reconnects — anything sent while BLE is down is simply not delivered to the BLE side (it still goes USB↔UART).
