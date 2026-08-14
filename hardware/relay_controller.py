"""
RelayController — safety-enforcing relay board driver.

Implements RelayControllerInterface using RelaySerial for real hardware.

Hardware safety rules (now enforced primarily by the MCU firmware; the PC
mirrors them so the requested state is well-defined before it is sent):
  1. At most ONE relay from RL1–RL16  (A1) may be active at a time.
  2. At most ONE relay from RL17–RL32 (A2) may be active at a time.
  3. At most ONE relay from RL37–RL40 (Group B — energizing-winding taps).
  4. Groups A (RL1–32) and B (RL37–40) are MUTUALLY EXCLUSIVE.
  5. RL33 (Gate A) closes automatically when any A1-relay is active.
  6. RL34 (Gate B) closes automatically when any A2-relay is active.
  7. RL35/RL36 close automatically when any Group-B relay is active.

The firmware owns the gates and exclusivity; the PC sends only selectable
relays (RL1–32, RL37–40) via SELECT and never the gate relays (RL33–36). Gate
state is mirrored locally only for the UI/diagram. When more than one relay per
group is requested a warning is logged and only the first is kept.
"""
import threading
import logging
from typing import Dict, List, Optional

from hardware.hardware_interface import (
    RelayControllerInterface, RelayState, HardwareStatus,
)
from hardware.relay_serial import RelaySerial
from hardware.protocol import (
    RL_A_MIN, RL_A_MAX, RL_B_MIN, RL_B_MAX, RL_B2_MIN, RL_B2_MAX,
    RL_GATE_A, RL_GATE_B, RL_GATE_B2, RELAY_COUNT,
    is_group_a, is_group_b, is_group_b2, is_gate,
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
            # Drop one relay, then re-apply whatever remains active.
            with self._lock:
                self._states[relay_id] = False
                active = [r for r, s in self._states.items()
                          if s and not is_gate(r)]
            return self.set_relays_safe(active)

    def set_all_relays(self, states: Dict[int, bool]) -> bool:
        """Set relays from a {relay_id: bool} map; enforces safety on the True set."""
        active = [r for r, s in states.items() if s]
        return self.set_relays_safe(active)

    def set_relays_safe(self, relay_ids: List[int]) -> bool:
        """
        Activate exactly the listed selectable relays (all others open).

        Gate relays in the request are ignored — the firmware closes them
        automatically. The PC tracks the implied gate state only for the UI.
        """
        selectable = self._enforce_group_safety(relay_ids)   # ≤1 A1, ≤1 A2, ≤1 B; no gates

        # Local state mirror (selectable relays + the gates the firmware will
        # close) so the diagram/relay panel reflect the live hardware state.
        active = set(selectable)
        if any(is_group_a(r) for r in selectable):
            active.add(RL_GATE_A)
        if any(is_group_b(r) for r in selectable):
            active.add(RL_GATE_B)
        if any(is_group_b2(r) for r in selectable):
            active.update(RL_GATE_B2)
        with self._lock:
            for i in range(1, RELAY_COUNT + 1):
                self._states[i] = (i in active)

        if self._serial.connected:
            return self._serial.apply(selectable)
        return True   # software mode

    def reset_all_relays(self) -> bool:
        with self._lock:
            for k in self._states:
                self._states[k] = False
        if self._serial.connected:
            return self._serial.clear()
        return True

    def emergency_stop(self) -> bool:
        # The firmware has no dedicated ESTOP — CLEAR opens everything.
        with self._lock:
            for k in self._states:
                self._states[k] = False
        if self._serial.connected:
            return self._serial.clear()
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

    def read_phase(self):
        """
        Read the phase-detect pin from the MCU (winding-polarity check).
            True  — in-phase (signal present)
            False — out-of-phase (polarity fault)
            None  — board not connected / no reply (unknown; skip the check)
        Only meaningful while a winding is energized and routed (call it inside
        the measurement window).
        """
        if not self._serial.connected:
            return None
        return self._serial.query_phase()

    @property
    def relay_count(self) -> int:
        return RELAY_COUNT

    # ── safety helpers ────────────────────────────────────────────────────

    @staticmethod
    def _enforce_group_safety(relay_ids: List[int]) -> List[int]:
        """
        Reduce a request to at most one A1-relay (RL1-16), one A2-relay
        (RL17-32) and one Group-B relay (RL37-40). Gate relays (RL33-36) are
        dropped — the firmware controls them.

        Group A (RL1-32) and Group B (RL37-40) are mutually exclusive: if both
        are requested, Group B wins (an energizing-winding tap measurement never
        needs an A relay) and the A relays are dropped with a warning.

        If duplicates exist within a subgroup, keep only the first and warn.
        """
        a_relays  = [r for r in relay_ids if is_group_a(r)]
        b_relays  = [r for r in relay_ids if is_group_b(r)]
        b2_relays = [r for r in relay_ids if is_group_b2(r)]
        dropped   = [r for r in relay_ids if is_gate(r)]

        if dropped:
            log.debug(f"Ignoring gate relays in request {dropped} — firmware-controlled")
        if len(a_relays) > 1:
            log.warning(
                f"SAFETY: {len(a_relays)} A1-relays requested {a_relays}; "
                f"only RL{a_relays[0]} will be activated"
            )
        if len(b_relays) > 1:
            log.warning(
                f"SAFETY: {len(b_relays)} A2-relays requested {b_relays}; "
                f"only RL{b_relays[0]} will be activated"
            )
        if len(b2_relays) > 1:
            log.warning(
                f"SAFETY: {len(b2_relays)} Group-B relays requested {b2_relays}; "
                f"only RL{b2_relays[0]} will be activated"
            )

        # Group A and Group B are mutually exclusive.
        if b2_relays and (a_relays or b_relays):
            log.warning(
                f"SAFETY: Group A {a_relays + b_relays} requested together with "
                f"Group B {b2_relays}; groups are mutually exclusive — "
                f"keeping Group B only"
            )
            a_relays = b_relays = []

        result: List[int] = []
        if a_relays:
            result.append(a_relays[0])
        if b_relays:
            result.append(b_relays[0])
        if b2_relays:
            result.append(b2_relays[0])
        return result
