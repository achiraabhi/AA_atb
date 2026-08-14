"""
Abstract base classes for all hardware interfaces.

Concrete implementations (real serial hardware drivers) must subclass these
ABCs.  The test engine and UI code depend only on these interfaces, never on
concrete drivers.

Relay board layout constants are also defined here so any module can
import them without pulling in a concrete implementation.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

# ── relay board layout constants ───────────────────────────────────────────
RELAY_COUNT  = 34    # total relay channels on the board
RL_A_MIN     = 1     # first Side-A relay
RL_A_MAX     = 16    # last  Side-A relay
RL_B_MIN     = 17    # first Side-B relay
RL_B_MAX     = 32    # last  Side-B relay
RL_GATE_A    = 33    # gate relay — voltmeter + bus
RL_GATE_B    = 34    # gate relay — voltmeter − bus


class RelayState(Enum):
    OPEN    = "OPEN"
    CLOSED  = "CLOSED"
    UNKNOWN = "UNKNOWN"
    FAULT   = "FAULT"


class HardwareStatus(Enum):
    CONNECTED     = "CONNECTED"
    DISCONNECTED  = "DISCONNECTED"
    ERROR         = "ERROR"
    INITIALIZING  = "INITIALIZING"


@dataclass
class VoltageReading:
    channel:   int
    voltage:   float
    timestamp: float
    valid:     bool = True
    error:     Optional[str] = None


@dataclass
class RelayStatus:
    relay_id:  int
    state:     RelayState
    timestamp: float
    error:     Optional[str] = None


@dataclass
class HardwareEvent:
    event_type: str   # "relay_change" | "voltage_read" | "error" | "connected" | "disconnected"
    data:       dict  = field(default_factory=dict)
    timestamp:  float = 0.0


# ── relay controller ABC ───────────────────────────────────────────────────

class RelayControllerInterface(ABC):
    """
    Abstract relay controller.

    All relay IDs are 1-based integers (RL1 = 1, RL34 = 34).
    Concrete implementations must enforce the group-safety rules defined in
    protocol.py (max one A-relay, max one B-relay, auto gate relays).
    """

    @abstractmethod
    def connect(self) -> bool:
        """Open connection to relay hardware. Returns True on success."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection to relay hardware."""

    @abstractmethod
    def set_relay(self, relay_id: int, state: bool) -> bool:
        """Set a single relay ON/OFF. Returns True on success."""

    @abstractmethod
    def set_all_relays(self, states: Dict[int, bool]) -> bool:
        """
        Apply a relay state map atomically.
        Safety enforcement (group exclusivity + gate relays) is performed
        internally before the command is sent to hardware.
        """

    @abstractmethod
    def reset_all_relays(self) -> bool:
        """Open all relays (safe state). Returns True on success."""

    @abstractmethod
    def get_relay_state(self, relay_id: int) -> RelayState:
        """Read current state of a relay."""

    @abstractmethod
    def get_status(self) -> HardwareStatus:
        """Return connection/health status."""

    def read_phase(self):
        """
        Optional: read the phase-detect pin (winding polarity).
        True = in-phase, False = out-of-phase, None = unknown/unsupported.
        Default is None so controllers without the feature degrade gracefully.
        """
        return None

    @property
    @abstractmethod
    def relay_count(self) -> int:
        """Number of relay channels (always 34 for the current board)."""


# ── voltage reader ABC ─────────────────────────────────────────────────────

class VoltageReaderInterface(ABC):
    """Abstract voltage measurement interface."""

    @abstractmethod
    def connect(self) -> bool:
        """Open connection to voltage measurement hardware."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection."""

    @abstractmethod
    def read_voltage(self, channel: int) -> VoltageReading:
        """
        Read voltage on a channel.
        For single-channel serial meters the channel argument is ignored.
        """

    @abstractmethod
    def read_all_channels(self) -> List[VoltageReading]:
        """Read all available channels."""

    @abstractmethod
    def get_status(self) -> HardwareStatus:
        """Return connection/health status."""

    @property
    @abstractmethod
    def channel_count(self) -> int:
        """Number of measurement channels."""


# ── top-level hardware manager ABC ────────────────────────────────────────

class HardwareManagerInterface(ABC):
    """
    Top-level hardware manager.
    Owns relay controller + voltage reader instances.
    The test engine and UI code depend only on this interface.
    """

    @property
    @abstractmethod
    def relays(self) -> RelayControllerInterface:
        """Relay controller."""

    @property
    @abstractmethod
    def voltmeter(self) -> VoltageReaderInterface:
        """Voltage reader."""

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize all hardware subsystems."""

    @abstractmethod
    def shutdown(self) -> None:
        """Safe shutdown: open all relays, close connections."""

    @abstractmethod
    def emergency_stop(self) -> None:
        """Immediately open all relays regardless of state."""

    @abstractmethod
    def health_check(self) -> Dict[str, HardwareStatus]:
        """Return health status dict for all subsystems."""
