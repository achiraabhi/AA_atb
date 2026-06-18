"""
RelayController — safety-enforcing relay board driver.

Implements RelayControllerInterface using RelaySerial for real hardware.

Hardware safety rules (enforced in software AND recommended in MCU firmware):
  1. At most ONE relay from RL1–RL16  (Side A) may be active at a time.
  2. At most ONE relay from RL17–RL32 (Side B) may be active at a time.
  3. RL33 (Gate A) automatically closes when any A-relay is active.
  4. RL34 (Gate B) automatically closes when any B-relay is active.

When violations are detected a warning is logged and only the first relay of
each group is kept — the driver never silently activates an unsafe state.
"""
import threading
import logging
from typing import Dict, List, Optional

from hardware.hardware_interface import (
    RelayControllerInterface, RelayState, HardwareStatus,
)
from hardware.relay_serial import RelaySerial
from hardware.protocol import (
    RL_A_MIN, RL_A_MAX, RL_B_MIN, RL_B_MAX,
    RL_GATE_A, RL_GATE_B, RELAY_COUNT,
    is_group_a, is_group_b,
)

log = logging.getLogger(__name__)


class RelayController(RelayControllerInterface):
    """
    Production relay controller.

    Relay IDs are 1-based (RL1 = 1, RL34 = 34) matching the hardware spec.
    """

    def __init__(self) -> None:
        self._serial  = RelaySerial()
        self._states: Dict[int, bool] = {i: False for i in range(1, RELAY_COUNT + 1)}
        self._lock    = threading.Lock()
        self._status  = HardwareStatus.DISCONNECTED

    # ── connection ────────────────────────────────────────────────────────

    def connect(self, port: str, baud: int = 115200) -> bool:
        ok = self._serial.connect(port, baud)
        self._status = HardwareStatus.CONNECTED if ok else HardwareStatus.ERROR
        return ok

    def disconnect(self) -> None:
        self.reset_all_relays()
        self._serial.disconnect()
        self._status = HardwareStatus.DISCONNECTED

    # ── core relay control ────────────────────────────────────────────────

    def set_relay(self, relay_id: int, state: bool) -> bool:
        """
        Set a single relay.  Setting ON merges with current state after safety
        checks.  Setting OFF simply removes it from the active set.
        """
        if state:
            with self._lock:
                current_active = [r for r, s in self._states.items() if s]
            return self.set_relays_safe(current_active + [relay_id])
        else:
            with self._lock:
                self._states[relay_id] = False
                active = [r for r, s in self._states.items() if s]
            if self._serial.connected:
                return self._serial.set_relays(active) if active else self._serial.clear_all()
            return True

    def set_all_relays(self, states: Dict[int, bool]) -> bool:
        """Set relays from a {relay_id: bool} map; enforces safety on the True set."""
        active = [r for r, s in states.items() if s]
        return self.set_relays_safe(active)

    def set_relays_safe(self, relay_ids: List[int]) -> bool:
        """
        Activate exactly the listed relays (all others open).
        Safety: enforce max-one-per-group and auto-add gate relays.
        """
        safe_ids  = self._enforce_group_safety(relay_ids)
        final_ids = self._add_gate_relays(safe_ids)

        with self._lock:
            for i in range(1, RELAY_COUNT + 1):
                self._states[i] = (i in final_ids)

        if self._serial.connected:
            return self._serial.set_relays(final_ids)
        return True   # software mode

    def reset_all_relays(self) -> bool:
        with self._lock:
            for k in self._states:
                self._states[k] = False
        if self._serial.connected:
            return self._serial.clear_all()
        return True

    def emergency_stop(self) -> bool:
        with self._lock:
            for k in self._states:
                self._states[k] = False
        if self._serial.connected:
            return self._serial.emergency_stop()
        return True

    # ── state queries ─────────────────────────────────────────────────────

    def get_relay_state(self, relay_id: int) -> RelayState:
        with self._lock:
            if relay_id not in self._states:
                return RelayState.UNKNOWN
            return RelayState.CLOSED if self._states[relay_id] else RelayState.OPEN

    def get_all_states(self) -> Dict[int, bool]:
        with self._lock:
            return dict(self._states)

    def get_status(self) -> HardwareStatus:
        return self._status

    @property
    def relay_count(self) -> int:
        return RELAY_COUNT

    # ── safety helpers ────────────────────────────────────────────────────

    @staticmethod
    def _enforce_group_safety(relay_ids: List[int]) -> List[int]:
        """
        Guarantee at most one A-relay (1-16) and one B-relay (17-32).
        If duplicates exist, keep only the first and log a warning.
        Gate relays (33, 34) pass through unchanged.
        """
        a_relays = [r for r in relay_ids if is_group_a(r)]
        b_relays = [r for r in relay_ids if is_group_b(r)]
        gates    = [r for r in relay_ids if r in (RL_GATE_A, RL_GATE_B)]

        if len(a_relays) > 1:
            log.warning(
                f"SAFETY: {len(a_relays)} A-relays requested {a_relays}; "
                f"only RL{a_relays[0]} will be activated"
            )
        if len(b_relays) > 1:
            log.warning(
                f"SAFETY: {len(b_relays)} B-relays requested {b_relays}; "
                f"only RL{b_relays[0]} will be activated"
            )

        result: List[int] = []
        if a_relays:
            result.append(a_relays[0])
        if b_relays:
            result.append(b_relays[0])
        result.extend(g for g in gates if g not in result)
        return result

    @staticmethod
    def _add_gate_relays(relay_ids: List[int]) -> List[int]:
        """Automatically add gate relays based on which groups are active."""
        result = list(relay_ids)
        if any(is_group_a(r) for r in relay_ids) and RL_GATE_A not in result:
            result.append(RL_GATE_A)
        if any(is_group_b(r) for r in relay_ids) and RL_GATE_B not in result:
            result.append(RL_GATE_B)
        return result
