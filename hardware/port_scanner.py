"""
Serial port scanner / classifier.

Probes each serial port to decide what kind of device is on it:
  - "voltmeter" — continuously streams VOLTAGE:<float> (or bare floats)
  - "relay"     — answers the relay-MCU PING handshake with OK
  - "unknown"   — opened but matched neither (or could not be opened)

This makes V1/V2/relay assignment independent of unstable /dev/ttyACM*
numbering: we detect by behaviour, not by port name.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

try:
    import serial
    from serial.tools import list_ports
    _SERIAL_OK = True
except ImportError:  # pragma: no cover
    _SERIAL_OK = False

from hardware.protocol import parse_voltage, build_status, RESP_STATUS_MARK

log = logging.getLogger(__name__)

# Arduino boards reset when the port opens (DTR); wait past that before reading.
_SETTLE_S = 1.5


def list_devices() -> List[Dict[str, str]]:
    """All serial ports on the host: [{device, description}]."""
    if not _SERIAL_OK:
        return []
    return [
        {"device": p.device, "description": p.description or ""}
        for p in sorted(list_ports.comports(), key=lambda x: x.device)
    ]


def classify_port(device: str, baud: int = 115200,
                  listen_s: float = 2.0) -> str:
    """
    Sniff a single port. Returns 'voltmeter', 'relay', or 'unknown'.

    Only call this on ports NOT already owned by the app — opening a port
    that another reader holds will steal bytes from it.
    """
    if not _SERIAL_OK:
        return "unknown"
    s = None
    try:
        s = serial.Serial()
        s.port     = device
        s.baudrate = baud
        s.timeout  = 0.5
        s.dsrdtr   = False
        s.rtscts   = False
        s.open()
    except Exception as exc:
        log.debug(f"classify_port({device}): cannot open — {exc}")
        if s is not None:
            try: s.close()
            except Exception: pass
        return "unknown"

    try:
        time.sleep(_SETTLE_S)
        try:
            s.reset_input_buffer()
        except Exception:
            pass

        # 1) Listen for a voltmeter's continuous stream.
        deadline = time.time() + listen_s
        while time.time() < deadline:
            line = s.readline().decode("ascii", errors="replace")
            if parse_voltage(line) is not None:
                return "voltmeter"

        # 2) Quiet so far — probe the relay MCU with STATUS and look for its
        #    status block ("===== RELAY STATUS =====").
        try:
            s.reset_input_buffer()
            s.write(build_status().encode("ascii"))
        except Exception:
            return "unknown"
        deadline = time.time() + 1.5
        while time.time() < deadline:
            line = s.readline().decode("ascii", errors="replace").strip().upper()
            if RESP_STATUS_MARK in line:
                return "relay"
        return "unknown"
    except Exception as exc:
        log.debug(f"classify_port({device}): probe error — {exc}")
        return "unknown"
    finally:
        try: s.close()
        except Exception: pass


def scan(skip: Optional[set] = None,
         known: Optional[Dict[str, str]] = None,
         baud: int = 115200) -> List[Dict[str, str]]:
    """
    Classify every serial port.

    skip  — ports the app already holds open; reported using `known` instead
            of probing (probing an open port steals its data).
    known — {device: type} for skipped ports.
    """
    skip  = skip or set()
    known = known or {}
    out: List[Dict[str, str]] = []
    for dev in list_devices():
        device = dev["device"]
        if device in skip:
            kind = known.get(device, "voltmeter")
        else:
            kind = classify_port(device, baud)
        out.append({
            "device":      device,
            "description": dev["description"],
            "type":        kind,
        })
    return out


def resolve_assignments(relay_cfg: Optional[str],
                        v1_cfg: Optional[str],
                        v2_cfg: Optional[str],
                        baud: int = 115200) -> Dict[str, object]:
    """
    Probe all ports once at startup and decide which port is the relay and
    which two are V1 / V2. Configured ports win when they match the detected
    type; otherwise detected devices are auto-assigned in a stable order.

    Returns {relay, v1, v2, types: {device: type}}.
    """
    types: Dict[str, str] = {}
    for dev in list_devices():
        types[dev["device"]] = classify_port(dev["device"], baud)

    relay_ports     = [d for d, t in types.items() if t == "relay"]
    voltmeter_ports = sorted(d for d, t in types.items() if t == "voltmeter")

    # Relay: prefer configured port if it really is a relay, else first found.
    relay = relay_cfg if relay_cfg in relay_ports else (relay_ports[0] if relay_ports else None)

    # Voltmeters: prefer configured ports when they are detected voltmeters,
    # then fill any remaining slot from the detected voltmeters in order.
    used: set = set()
    def _pick(cfg: Optional[str]) -> Optional[str]:
        if cfg in voltmeter_ports and cfg not in used:
            used.add(cfg)
            return cfg
        for d in voltmeter_ports:
            if d not in used:
                used.add(d)
                return d
        return None

    v1 = _pick(v1_cfg)
    v2 = _pick(v2_cfg)

    return {"relay": relay, "v1": v1, "v2": v2, "types": types}
