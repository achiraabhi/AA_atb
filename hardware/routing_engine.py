"""
Relay routing engine.

Converts a logical measurement request (which two nodes to connect across the
voltmeter) into the physical relay activation list [RL_a, RL_b, RL33, RL34].

Hardware layout reminder:
  RL1  – RL16 : Side-A relays  → voltmeter + bus
  RL17 – RL32 : Side-B relays  → voltmeter − bus
  RL33         : Gate A  (auto-closes when any A-relay is active)
  RL34         : Gate B  (auto-closes when any B-relay is active)

Safety (enforced here AND again in RelayController):
  At most ONE A-relay and ONE B-relay may be active simultaneously.

JSON winding fields used:
  relay_a : int | None   ← RL1-16 connected to start_pin   (voltmeter + probe)
  relay_b : int | None   ← RL17-32 connected to end_pin    (voltmeter − probe)

JSON tap dict fields used:
  relay_b : int | None   ← RL17-32 connected to tap_pin   (voltmeter − probe)

A tap gets a SINGLE relay on the B-side (− bus). It is normally measured
start→tap: winding.relay_a (start pin, + bus) paired with tap.relay_b.

Energizing winding — special case (Group B, RL37-40)
----------------------------------------------------
Only the energizing winding's MAIN WIRES (start_pin/end_pin) are external:
they carry mains and are never switched, so they take NO relays. Its start
node is permanently wired to the voltmeter + bus.

Its TAPS, however, ARE measured — and they are routed through **Group B
(RL37-40)**, NOT through the A2 group (RL17-32) used by normal windings.
Because the + probe already sits on the start node, measuring one of these
taps closes ONLY that tap's Group B relay; the firmware closes gates RL35/36.

    normal winding tap     → [relay_a, relay_b, RL33, RL34]   (Group A)
    energizing winding tap → [relay_b2, RL35, RL36]           (Group B)

Groups A and B are mutually exclusive — a Group B route never carries an
A relay.
"""
from typing import Dict, List, Optional

from hardware.protocol import (
    RL_A_MIN, RL_A_MAX, RL_B_MIN, RL_B_MAX, RL_B2_MIN, RL_B2_MAX,
    RL_GATE_A, RL_GATE_B, RL_GATE_B2,
)


class RoutingError(ValueError):
    """Raised when a valid measurement path cannot be constructed."""


class RoutingEngine:
    """
    Stateless engine that produces relay-ID lists from winding/tap descriptors.

    All public methods return a plain list of integer relay IDs to close.
    The list always includes the gate relays (33, 34) so callers do not need
    to add them manually.
    """

    # ── public API ────────────────────────────────────────────────────────

    def resolve_winding(self, winding) -> List[int]:
        """
        Full winding measurement: start_pin (relay_a) → end_pin (relay_b).
        """
        relay_a = winding.relay_a
        relay_b = winding.relay_b
        if relay_a is None or relay_b is None:
            raise RoutingError(
                f"Winding '{winding.id}' needs both relay_a (RL1-16) and "
                f"relay_b (RL17-32) for a full measurement."
            )
        return self._build(relay_a, relay_b, context=f"winding {winding.id}")

    def resolve_tap_from_start(self, winding, tap: dict,
                               energizing: bool = False) -> List[int]:
        """
        Tap measurement: start_pin → tap_pin.
        Uses winding.relay_a (A-side) and tap['relay_b'] (B-side).

        When ``energizing`` is True the winding is the energizing winding: its
        main wires are external and its start is permanently wired to the + bus,
        so no A relay exists (or is needed) — only the tap's Group B relay
        (RL37-40) is closed.
        """
        relay_a = winding.relay_a
        relay_b = tap.get("relay_b")

        if energizing:
            # Energizing winding: + probe is on the start node via permanent
            # wiring, so the A side is not switched. relay_a must be unset and
            # the tap's relay must come from Group B (RL37-40).
            if relay_a is not None:
                raise RoutingError(
                    f"Energizing winding '{winding.id}' must not have a relay_a — "
                    f"its main wires are external (start is hard-wired to the + bus)."
                )
            if relay_b is None:
                raise RoutingError(
                    f"tap measurement for energizing winding '{winding.id}' needs "
                    f"the tap's relay (RL{RL_B2_MIN}-RL{RL_B2_MAX}, Group B)."
                )
            return self._build_b2_only(
                relay_b, context=f"energizing winding {winding.id} tap")

        if relay_a is None or relay_b is None:
            raise RoutingError(
                f"start→tap measurement for winding '{winding.id}' needs "
                f"winding.relay_a and tap.relay_b."
            )
        return self._build(relay_a, relay_b, context=f"winding {winding.id} tap start→tap")

    def resolve_tap_auto(self, winding, tap: dict,
                         energizing: bool = False) -> List[int]:
        """
        Route a tap. A tap has a single relay.

          • normal winding     — start→tap: winding.relay_a (A1) + tap.relay_b (A2)
          • energizing winding — only the tap's Group B relay (RL37-40); the
            start is hard-wired to the + bus so no A relay is used.

        Raises RoutingError if the route is unavailable.
        """
        if energizing:
            return self.resolve_tap_from_start(winding, tap, energizing=True)
        if winding.relay_a is not None and tap.get("relay_b") is not None:
            return self.resolve_tap_from_start(winding, tap)
        raise RoutingError(
            f"No valid route for tap on winding '{winding.id}'. "
            f"Needs winding.relay_a and the tap's relay (relay_b, RL17-32)."
        )

    def relay_map_from_list(self, relay_ids: List[int]) -> Dict[int, bool]:
        """Return {relay_id: True} dict suitable for StateManager relay_states."""
        return {r: True for r in relay_ids}

    # ── internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _build(relay_a: int, relay_b: int, context: str = "") -> List[int]:
        if not (RL_A_MIN <= relay_a <= RL_A_MAX):
            raise RoutingError(
                f"relay_a={relay_a} is outside A-group range "
                f"RL{RL_A_MIN}–RL{RL_A_MAX} [{context}]"
            )
        if not (RL_B_MIN <= relay_b <= RL_B_MAX):
            raise RoutingError(
                f"relay_b={relay_b} is outside B-group range "
                f"RL{RL_B_MIN}–RL{RL_B_MAX} [{context}]"
            )
        return [relay_a, relay_b, RL_GATE_A, RL_GATE_B]

    @staticmethod
    def _build_b2_only(relay_b2: int, context: str = "") -> List[int]:
        """
        Group-B-only path (RL37-40) — the energizing winding's taps. Its + probe
        reaches the start node through permanent wiring instead of an A relay, so
        no Group A relay is closed. The firmware closes gates RL35/36.
        """
        if not (RL_B2_MIN <= relay_b2 <= RL_B2_MAX):
            raise RoutingError(
                f"relay={relay_b2} is outside Group-B range "
                f"RL{RL_B2_MIN}–RL{RL_B2_MAX} — the energizing winding's taps "
                f"must use Group B, not the A2 group [{context}]"
            )
        return [relay_b2, *RL_GATE_B2]
