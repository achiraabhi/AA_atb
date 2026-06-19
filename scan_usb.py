#!/usr/bin/env python3
"""Scan USB HID devices and detect a UNI-T DMM (CP2110 USB-UART bridge).

The UT61B+ / UT61E+ / UT161x family connect through a Silicon Labs CP2110
HID-to-UART bridge: VID=0x10c4, PID=0xEA80.
"""
import hid

# Known USB-UART bridges used by the UNI-T "+" DMM family.
BRIDGES = {
    (0x10c4, 0xEA80): "Silicon Labs CP2110 (UT61E+ and most models)",
    (0x1a86, 0xE429): "WCH HID-UART bridge (e.g. UT61B+)",
}


def main():
    devices = hid.enumerate()
    print(f"Found {len(devices)} HID device(s):\n")
    found = []
    for d in devices:
        vid, pid = d["vendor_id"], d["product_id"]
        manuf = d.get("manufacturer_string") or "?"
        prod = d.get("product_string") or "?"
        mark = ""
        if (vid, pid) in BRIDGES:
            mark = f"  <== UNI-T DMM bridge: {BRIDGES[(vid, pid)]}"
            found.append(d)
        print(f"  {vid:04x}:{pid:04x}  {manuf} - {prod}{mark}")

    print()
    if found:
        f = found[0]
        print(f"OK: detected DMM bridge {f['vendor_id']:04x}:{f['product_id']:04x}.")
        print("You can now run:  python readDMM.py")
    else:
        print("NOT found: no known UNI-T DMM USB bridge.")
        print("Looked for:")
        for (vid, pid), name in BRIDGES.items():
            print(f"  {vid:04x}:{pid:04x}  {name}")
        print("Make sure the UT61B+ is powered on and connected via its USB cable.")


if __name__ == "__main__":
    main()
