"""
Serial protocol constants and message builders.

Relay MCU (bidirectional):
  PC → MCU  :  SET_RELAYS:1,2,33,34\r\n | CLEAR_ALL\r\n | ESTOP\r\n | PING\r\n
  MCU → PC  :  OK\r\n | ERROR:<reason>\r\n

Voltage Meter (continuous output, PC reads only):
  Meter → PC:  18.42\r\n   OR   VOLTAGE:18.42\r\n

Relay board layout:
  RL1  – RL16  : Side-A relays  (connect a node to voltmeter + bus)
  RL17 – RL32  : Side-B relays  (connect a node to voltmeter − bus)
  RL33          : Gate relay A   (connects + bus to voltmeter input)
  RL34          : Gate relay B   (connects − bus to voltmeter input)
"""
from typing import List, Optional

# ── relay group boundaries ─────────────────────────────────────────────────
RELAY_COUNT  = 34

RL_A_MIN  = 1
RL_A_MAX  = 16
RL_B_MIN  = 17
RL_B_MAX  = 32
RL_GATE_A = 33   # auto-closes whenever any A-relay is active
RL_GATE_B = 34   # auto-closes whenever any B-relay is active

# ── serial defaults ────────────────────────────────────────────────────────
DEFAULT_BAUD_MCU   = 115200
DEFAULT_BAUD_METER = 9600

# ── relay MCU command strings ──────────────────────────────────────────────
CMD_SET_RELAYS = "SET_RELAYS"
CMD_CLEAR_ALL  = "CLEAR_ALL"
CMD_ESTOP      = "ESTOP"
CMD_PING       = "PING"

RESP_OK    = "OK"
RESP_ERROR = "ERROR"


# ── group queries ──────────────────────────────────────────────────────────

def is_group_a(relay_id: int) -> bool:
    return RL_A_MIN <= relay_id <= RL_A_MAX


def is_group_b(relay_id: int) -> bool:
    return RL_B_MIN <= relay_id <= RL_B_MAX


def is_gate(relay_id: int) -> bool:
    return relay_id in (RL_GATE_A, RL_GATE_B)


def relay_group_label(relay_id: int) -> str:
    if is_group_a(relay_id):
        return "A"
    if is_group_b(relay_id):
        return "B"
    if relay_id == RL_GATE_A:
        return "GA"
    if relay_id == RL_GATE_B:
        return "GB"
    return "?"


# ── message builders ───────────────────────────────────────────────────────

def build_set_relays(relay_ids: List[int]) -> str:
    ids_str = ",".join(str(r) for r in sorted(set(int(r) for r in relay_ids)))
    return f"SET_RELAYS:{ids_str}\r\n"


def build_clear_all() -> str:
    return "CLEAR_ALL\r\n"


def build_estop() -> str:
    return "ESTOP\r\n"


def build_ping() -> str:
    return "PING\r\n"


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
