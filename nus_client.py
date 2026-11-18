#!/usr/bin/env python3
"""
NUS client for the XIAO ESP32-C6 BLE/UART bridge (src/main.cpp).

Talks to the Nordic UART Service the firmware advertises:
    service  6e400001-b5a3-f393-e0a9-e50e24dcca9e
    write    6e400002-...   host  -> board  (USB + GPIO16)
    notify   6e400003-...   board -> host

No pairing or bonding required: the firmware exposes the characteristics
without encryption, so this connects straight through.

Usage:
    python3 nus_client.py                    # scan, pick device, interactive
    python3 nus_client.py --scan-only        # list NUS devices, don't connect
    python3 nus_client.py -a 54:32:04:21:83:AA
    python3 nus_client.py -n BLESerialfffe21

In the REPL: type and press enter to send. /hex sends raw bytes, /quit exits.
Incoming notifications are written to stdout verbatim with no added newlines,
because the bridge chunks at 20 bytes and a chunk boundary can fall mid-line.
"""

import argparse
import asyncio
import sys

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # host -> board
CHAR_NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # board -> host


def out(msg=""):
    """Status line -> stderr, so stdout stays a clean byte stream."""
    print(msg, file=sys.stderr, flush=True)


async def scan_for_devices(timeout, name_filter):
    """Return [(address, local_name, rssi)] for NUS devices, or all if no filter."""
    try:
        from bleak import BleakScanner
    except ImportError:
        sys.exit("bleak is not installed. Run:  pipx install bleak")

    out(f"scanning {timeout:.0f}s for BLE devices...")
    try:
        # bleak >= 0.21 returns {addr: (device, advertisement)}
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        rows = []
        for addr, (device, adv) in found.items():
            name = adv.local_name or device.name or ""
            rows.append((addr, name, getattr(adv, "rssi", None)))
    except TypeError:
        # older bleak: returns a list of BLEDevice
        found = await BleakScanner.discover(timeout=timeout)
        rows = [(d.address, d.name or "", None) for d in found]

    if name_filter:
        rows = [r for r in rows if name_filter.lower() in r[1].lower()]

    rows.sort(key=lambda r: (r[1] or "").lower())
    return rows


async def connect_and_run(address, timeout):
    try:
        from bleak import BleakClient
    except ImportError:
        sys.exit("bleak is not installed. Run:  pipx install bleak")

    client = BleakClient(address, timeout=timeout)
    out(f"connecting to {address}...")
    await client.connect()
    if not client.is_connected:
        sys.exit(f"failed to connect to {address}")

    out(f"connected. negotiated MTU: {getattr(client, 'mtu_size', 'unknown')}")

    # Resolve the two characteristics. Do not assume handle order or the order
    # the firmware created them in -- look them up by UUID.
    write_char = None
    notify_char = None
    for service in client.services:
        for char in service.characteristics:
            u = char.uuid.lower()
            if u == CHAR_WRITE_UUID:
                write_char = char
            elif u == CHAR_NOTIFY_UUID:
                notify_char = char

    if notify_char is None:
        await client.disconnect()
        sys.exit(
            f"{address} does not expose NUS notify char {CHAR_NOTIFY_UUID}.\n"
            "Wrong device, or it is not running this firmware."
        )
    if write_char is None:
        out(f"WARNING: no NUS write char {CHAR_WRITE_UUID}; receive-only.")

    received = bytearray()

    def on_notify(sender, data):
        # Verbatim, no added newline: a 20-byte chunk can split a line or a
        # UTF-8 sequence, and the bridge has no backlog for a reconnect.
        received.extend(data)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    await client.start_notify(notify_char, on_notify)
    out("subscribed to notifications. Type to send, /help for commands.\n")

    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":  # EOF (Ctrl-D or closed stdin)
                break
            line = line.rstrip("\n")

            if line == "/quit" or line == "/exit":
                break
            if line == "/help":
                out(
                    "  /hex <hexbytes>   send raw bytes, e.g. /hex 41 42 0a\n"
                    "  /quit            disconnect and exit"
                )
                continue
            if line.startswith("/hex"):
                try:
                    payload = bytes.fromhex(line[4:].replace(" ", ""))
                except ValueError as e:
                    out(f"bad hex: {e}")
                    continue
            else:
                payload = line.encode("utf-8") + b"\n"

            if write_char is None:
                out("cannot send: no write characteristic")
                continue
            try:
                # firmware declares WRITE_NR, so no round trip needed
                await client.write_gatt_char(write_char, payload, response=False)
            except Exception as e:
                out(f"write failed: {e}")
    finally:
        # Always tear down cleanly: on /quit, EOF, Ctrl-C, or an unhandled error.
        try:
            await client.stop_notify(notify_char)
        except Exception:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass
    out(f"\ndisconnected. received {len(received)} bytes.")


async def main():
    ap = argparse.ArgumentParser(description="NUS terminal for the ESP32-C6 bridge")
    ap.add_argument("-a", "--address", help="BLE address, e.g. 54:32:04:21:83:AA")
    ap.add_argument("-n", "--name", help="substring of the advertised name")
    ap.add_argument("-t", "--timeout", type=float, default=10.0, help="scan seconds")
    ap.add_argument("--connect-timeout", type=float, default=15.0)
    ap.add_argument("--scan-only", action="store_true", help="list devices and exit")
    args = ap.parse_args()

    if not sys.stdin.isatty():
        out("note: stdin is not a TTY; piping input works but Ctrl-C is less reliable.")

    if args.address:
        address = args.address
    else:
        rows = await scan_for_devices(args.timeout, args.name)
        if not rows:
            hint = f" matching {args.name!r}" if args.name else ""
            sys.exit(f"no BLE devices found{hint}. Try --scan-only, or raise --timeout.")

        if args.scan_only:
            for addr, name, rssi in rows:
                r = f" {rssi} dBm" if rssi is not None else ""
                print(f"{addr}  {name or '(no name)'}{r}")
            return

        out("")
        for i, (addr, name, rssi) in enumerate(rows, 1):
            r = f" {rssi} dBm" if rssi is not None else ""
            out(f"  {i:2d}. {addr}  {name or '(no name)'}{r}")
        out("")

        # Default to the first BLESerial* match when one is present.
        pick = 1
        preferred = next(
            (i for i, r in enumerate(rows, 1) if r[1].lower().startswith("bleserial")),
            None,
        )
        if preferred is not None:
            pick = preferred
        if len(rows) > 1:
            raw = input(f"select [1-{len(rows)}, default {pick}]: ").strip()
            if raw:
                pick = int(raw)
        address = rows[pick - 1][0]

    await connect_and_run(address, args.connect_timeout)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        out("\ninterrupted.")
    except Exception as e:
        sys.exit(f"error: {e}")
