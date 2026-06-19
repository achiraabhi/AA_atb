#!/usr/bin/env python3
"""Continuously read measurements from a UNI-T UT61B+ (or compatible) over USB.

Works like a serial monitor: it polls the meter on a fixed interval and prints
each reading with a timestamp.

The UT61B+ exposes its data over a WCH HID-UART bridge (USB id 1a86:e429);
other "+" models use a Silicon Labs CP2110 (10c4:ea80). The driver in
`ut61eplus/ut61eplus.py` auto-detects either one.

Examples:
    python read_continuous.py            # print readings ~5x/second
    python read_continuous.py -i 1.0     # one reading per second

Press Ctrl+C to stop.
"""
import argparse
import sys
import time
from datetime import datetime

from ut61eplus import UT61EPLUS


def format_value(m):
    """Return a human string for the measurement value (handles overload)."""
    if m.overload:
        return "OL (overload)"
    return f"{m.value} {m.unit}"


def main():
    parser = argparse.ArgumentParser(
        description="Continuously read a UT61B+ multimeter over USB.")
    parser.add_argument("-i", "--interval", type=float, default=0.2,
                        help="seconds between readings (default: 0.2)")
    args = parser.parse_args()

    print("Opening multimeter ...", file=sys.stderr)
    dmm = UT61EPLUS()
    bridge = "WCH (UT61B+)" if dmm._wch else "CP2110"
    print(f"Connected to {dmm.getName()} via {bridge} bridge.", file=sys.stderr)
    print("Reading... press Ctrl+C to stop.\n", file=sys.stderr)

    try:
        while True:
            m = dmm.takeMeasurement()
            if m is None:
                # checksum mismatch / partial read - skip this sample
                time.sleep(args.interval)
                continue

            ts = datetime.now()
            flags = []
            if m.isDC:
                flags.append("DC")
            if m.isAuto:
                flags.append("AUTO")
            if m.isHold:
                flags.append("HOLD")
            if m.isRel:
                flags.append("REL")
            if m.hasBatteryWarning:
                flags.append("BATT")
            flag_str = " ".join(flags)

            print(f"{ts:%H:%M:%S.%f}  {m.mode:>6}  "
                  f"{format_value(m):>14}   {flag_str}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
