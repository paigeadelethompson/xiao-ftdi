# AGENTS.md

ESP32-C6 USB↔UART bridge firmware (`src/main.cpp`) + Python/bleak BLE host terminal (`nus_client.py`). No tests, no lint, no CI — **`pio run` succeeding is the verification step.** Flash with `pio run -t upload`; there is no other release/deploy flow.

## Environment gotchas (this machine)

- `pio` is **not on PATH** here; it works as `/root/.local/bin/pio` (PlatformIO Core 6.2.0, installed via pipx). `pipx list` reports its shims broken. Elsewhere: `pipx install platformio`.
- `bleak` is **not installed** — `nus_client.py` exits with `pipx install bleak` hint until it is.
- `pipx` apps dir is `/root/.local/bin` but PATH is `/root/.opencode/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`.

## platformio.ini — do not "fix" these

- Platform is the **pioarduino** fork, pinned by GitHub release-zip URL (`https://github.com/pioarduino/platform-espressif32/releases/download/stable/platform-espressif32.zip`), not the registry. The registry platform lacks working Arduino-ESP32 C6 support; swapping it breaks the build.
- Board is `seeed_xiao_esp32c6`; framework `arduino`.
- `build_flags` set `ARDUINO_USB_MODE=1` and `ARDUINO_USB_CDC_ON_BOOT=1` — required for `Serial` to enumerate over the built-in USB-C (no USB↔serial chip on this board). Removing them makes the board flash but go silent.
- First `pio run` downloads the platform + toolchain from the network; it is not cached in the repo. `.pio/` is gitignored.

## Firmware facts that are easy to break (src/main.cpp)

- `Serial1.setRxBufferSize(4096)` **must precede** `Serial1.begin(...)` — it is a no-op once the driver installs.
- UART pins: RX = GPIO17 (D7), TX = GPIO16 (D6); 115200 8N1; 3.3V logic.
- LED (GPIO15) is **active-low**; `ledOn()` writes LOW. Boot pattern is 3 short + 1 long flash.
- BLE notify chunk is **20 bytes** (default-MTU-safe); `BLEDevice::setMTU(512)` is requested but clients may not negotiate up — don't raise the chunk size without checking.
- Write characteristic is `WRITE | WRITE_NR`; the host client writes with `response=False`. Keep these in sync.
- The BLE→app ring buffer is **512 bytes and drops on full** (no backpressure, no error signal). Any pacing/flow-control change must account for this.
- Advertised name is `BLESerial` + last 3 MAC bytes (printed from `mac >> 24/32/40`) — `nus_client.py` defaults to the first `BLESerial*` scan match, so don't rename the prefix without updating the client.
- After a BLE disconnect, advertising restarts after a fixed 500 ms `delay(500)` in `loop()`.
- RX callback copies with `value.length()` bytes from `c_str()`, so embedded NULs survive — don't switch to `strlen`.

## nus_client.py contract

- **stdout is a raw byte stream** (notifications verbatim, no added newlines — chunk boundaries can split lines/UTF-8); all status/diagnostics go to **stderr**. Never `print()` to stdout for status.
- Notify data arrives in ≤20-byte chunks regardless of what the sender queued; don't add line-based reassembly.
- bleak ≥ 0.21 (`return_adv=True`) vs older API is handled with a TypeError fallback — preserve both paths.
- Resolves characteristics by UUID, not by discovery order — keep it that way.
- `/quit`, `/exit`, EOF (Ctrl-D) and Ctrl-C all tear down `stop_notify`/`disconnect` in a `finally`.

## Cross-cutting

- Firmware and client are one protocol. Any change to UUIDs, chunking, naming, or write-mode must touch **both** files in the same commit.
- There is no hardware-in-the-loop test; "it builds" ≠ "it bridges". For bridge changes, say so in the commit message if you could not test on hardware.
