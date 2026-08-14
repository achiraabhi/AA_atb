"""
Serial protocol constants and message builders.

Relay MCU (SELECT/CLEAR/STATUS firmware on the Arduino Mega 2560):
  PC → MCU  :  SELECT <n>\r\n | CLEAR\r\n | STATUS\r\n
  MCU → PC  :  "Selected Relay: <n>" | "Matrix Cleared" | a STATUS block.
               Errors come back as a line containing "ERROR".

  The firmware owns gate control, subgroup exclusivity (one of RL1-16, one of
  RL17-32, one of RL37-40) and group exclusivity. It sets ONE relay per SELECT
  and keeps a prior subgroup selection active, so a measurement that needs both
  an A-side and a B-side relay is issued as two SELECTs. The PC therefore:
    - sends ONLY selectable relays (never the gate relays RL33-36), and
    - never lists the gates explicitly — the firmware closes them itself.

Voltage Meter (continuous output, PC reads only):
  Meter → PC:  18.42\r\n   OR   VOLTAGE:18.42\r\n

Relay board layout (PC view):
  RL1  – RL16  : Side-A relays  (connect a node to voltmeter + bus)  ≙ firmware A1
  RL17 – RL32  : Side-B relays  (connect a node to voltmeter − bus)  ≙ firmware A2
  RL33          : Gate relay A   (auto, firmware-controlled)
  RL34          : Gate relay B   (auto, firmware-controlled)
  RL35, RL36    : Group-B gates  (auto, firmware-controlled)
  RL37 – RL40   : Group B relays — the ENERGIZING winding's TAP nodes.

Group A (RL1-32) vs Group B (RL37-40)
-------------------------------------
Group A measures a normal winding: one A1 (+ bus) + one A2 (− bus).

Group B measures a tap of the ENERGIZING winding. Only that winding's main
wires are external (mains, never switched) — its start is hard-wired to the
voltmeter + bus — so measuring one of its taps closes ONLY that tap's Group B
relay (RL37-40); the firmware closes gates RL35/36 with it.

Groups A and B are MUTUALLY EXCLUSIVE — never energize an A relay and a B
relay at the same time. (Firmware enforces this; RelayController mirrors it.)
"""
from typing import List, Optional

# ── relay group boundaries ─────────────────────────────────────────────────
RELAY_COUNT  = 40

RL_A_MIN  = 1
RL_A_MAX  = 16
RL_B_MIN  = 17
RL_B_MAX  = 32
RL_GATE_A = 33   # auto-closes whenever any A1-relay is active
RL_GATE_B = 34   # auto-closes whenever any A2-relay is active

# Group B — energizing-winding tap measurement (excitation domain).
RL_B2_MIN  = 37
RL_B2_MAX  = 40
RL_GATE_B2 = (35, 36)   # auto-close whenever any Group-B relay is active

# ── serial defaults ────────────────────────────────────────────────────────
DEFAULT_BAUD_MCU   = 115200
DEFAULT_BAUD_METER = 9600

# ── relay MCU command strings ──────────────────────────────────────────────
CMD_SELECT = "SELECT"
CMD_CLEAR  = "CLEAR"
CMD_STATUS = "STATUS"
CMD_PHASE  = "PHASE"    # query the phase-detect spare pin (winding polarity)

# The firmware does not reply "OK"; a command succeeds unless its reply contains
# this marker. STATUS replies include this header (used for port detection).
RESP_ERROR        = "ERROR"
RESP_STATUS_MARK  = "RELAY STATUS"

# Reserved gate relays the PC must never send (firmware rejects them and
# controls them automatically).
GATE_RELAYS = (33, 34, 35, 36)


def is_protected_gate(relay_id: int) -> bool:
    return relay_id in GATE_RELAYS


def is_selectable(relay_id: int) -> bool:
    """True if a relay may be sent via SELECT (A1, A2 or Group B; not a gate)."""
    return is_group_a(relay_id) or is_group_b(relay_id) or is_group_b2(relay_id)


# ── group queries ──────────────────────────────────────────────────────────

def is_group_a(relay_id: int) -> bool:
    """A1 — measurement winding START nodes (voltmeter + bus)."""
    return RL_A_MIN <= relay_id <= RL_A_MAX


def is_group_b(relay_id: int) -> bool:
    """A2 — measurement winding END / TAP nodes (voltmeter − bus)."""
    return RL_B_MIN <= relay_id <= RL_B_MAX


def is_group_b2(relay_id: int) -> bool:
    """Group B — ENERGIZING winding TAP nodes (RL37-40, excitation domain)."""
    return RL_B2_MIN <= relay_id <= RL_B2_MAX


def is_gate(relay_id: int) -> bool:
    return relay_id in GATE_RELAYS


def relay_group_label(relay_id: int) -> str:
    if is_group_a(relay_id):
        return "A"
    if is_group_b(relay_id):
        return "B"
    if is_group_b2(relay_id):
        return "B2"
    if relay_id == RL_GATE_A:
        return "GA"
    if relay_id == RL_GATE_B:
        return "GB"
    if relay_id in RL_GATE_B2:
        return "GB2"
    return "?"


# ── message builders ───────────────────────────────────────────────────────

def build_select(relay_id: int) -> str:
    """Select one relay. The firmware applies exclusivity + gates itself."""
    return f"SELECT {int(relay_id)}\r\n"


def build_clear() -> str:
    """Turn every relay (and gate) OFF."""
    return "CLEAR\r\n"


def build_status() -> str:
    return "STATUS\r\n"


def build_phase() -> str:
    """Ask the MCU for the phase-detect pin state (winding polarity check)."""
    return "PHASE\r\n"


def parse_phase(line: str):
    """
    Interpret the MCU's phase reply. The phase-detect circuit drives a spare MCU
    pin like an LED indicator: signal present ⇒ the measured winding is IN-PHASE
    with the energizing winding; no signal ⇒ OUT-OF-PHASE (a polarity fault).

    Accepts, case-insensitively:
        "PHASE:1" / "PHASE 1" / "1" / "IN"  / "INPHASE"      → True  (in-phase)
        "PHASE:0" / "PHASE 0" / "0" / "OUT" / "OUTOFPHASE"   → False (out-of-phase)
    Returns None if the line carries no recognizable phase token (e.g. an older
    firmware that doesn't answer PHASE) so the caller can treat it as "unknown".
    """
    if not line:
        return None
    t = line.strip().upper()
    # Strip an optional "PHASE" prefix and separators.
    if t.startswith("PHASE"):
        t = t[len("PHASE"):].lstrip(" :=").strip()
    if t in ("1", "IN", "INPHASE", "IN-PHASE", "TRUE", "OK", "PASS"):
        return True
    if t in ("0", "OUT", "OUTOFPHASE", "OUT-OF-PHASE", "FALSE", "FAIL"):
        return False
    return None


# ── voltage parser ─────────────────────────────────────────────────────────

def parse_voltage(line: str) -> Optional[float]:
    """
    Parse a voltage value from a meter serial line.
    Accepts: "18.42" or "VOLTAGE:18.42" (both with optional whitespace).
    Returns None if the line is not a valid voltage reading.
    """
    line = line.strip()
    if line.upper().startswith("VOLTAGE:"):
        candidate = line[8:]
    else:
        candidate = line
    try:
        return float(candidate)
    except ValueError:
        return None
