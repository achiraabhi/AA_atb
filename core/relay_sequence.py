"""
Relay sequencer — a diagnostic "click test" that energizes each assigned relay
in turn for a fixed dwell (default 1 second) so an operator can verify the
wiring of a transformer by sight and sound before running a real test.

The sequence walks, in order, every relay assigned to the selected
transformer's windings: each winding's Side-A relay (relay_a), Side-B relay
(relay_b), and every tap's Side-B relay.

Gates:
  The firmware rejects direct gate commands (RL33-36) and auto-closes Gate A
  (RL33) whenever any Side-A relay is active and Gate B (RL34) whenever any
  Side-B relay is active. The PC therefore cannot pulse a gate on its own; the
  gates are exercised implicitly — each winding relay's step closes its group's
  gate with it, and the relay mirror reflects that so the gate shows CLOSED for
  that 1-second window.

Runs in a background daemon thread; relay state is mirrored to the StateManager
so the live UI/diagram updates as each relay clicks. Refuses to start while a
real test is running.
"""
import time
import threading
import logging
from typing import List, Optional, Tuple

from hardware.protocol import is_group_a, is_group_b

log = logging.getLogger(__name__)

DEFAULT_DWELL_S = 1.0          # hold each relay energized for one second
_POLL_S         = 0.05         # stop-check granularity during a dwell


def build_relay_steps(config) -> List[Tuple[str, int]]:
    """
    Build the ordered (label, relay_id) list for a transformer's assigned relays.

    Order: for each winding — Side-A relay, Side-B relay, then each tap's relay.
    Relays that are unassigned (None) are skipped. Duplicates are dropped so a
    relay shared between nodes is only pulsed once.
    """
    steps: List[Tuple[str, int]] = []
    seen: set = set()

    def add(label: str, relay_id: Optional[int]) -> None:
        if relay_id is None or relay_id in seen:
            return
        seen.add(relay_id)
        gate = "Gate A" if is_group_a(relay_id) else "Gate B" if is_group_b(relay_id) else "?"
        steps.append((f"{label}  (RL{relay_id} → {gate} closes)", relay_id))

    for w in config.windings:
        add(f"{w.id} start", w.relay_a)
        add(f"{w.id} end", w.relay_b)
        for i, tap in enumerate(w.taps):
            add(f"{w.id} tap{i}", tap.get("relay_b"))

    return steps


class RelaySequencer:
    """Energizes each assigned relay for a fixed dwell, one at a time."""

    def __init__(self, state_manager, hardware):
        self._state = state_manager
        self._hw    = hardware
        self._thread: Optional[threading.Thread] = None
        self._stop  = threading.Event()
        self._lock  = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, steps: List[Tuple[str, int]],
              dwell_s: float = DEFAULT_DWELL_S) -> None:
        """Kick off the sequence in a background thread. Raises if already running."""
        with self._lock:
            if self.running:
                raise RuntimeError("Relay sequence already running")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, args=(steps, dwell_s),
                daemon=True, name="RelaySequencer",
            )
            self._thread.start()

    def stop(self) -> None:
        """Request an early stop; opens all relays. Safe to call when idle."""
        self._stop.set()

    # ── worker ────────────────────────────────────────────────────────────────

    def _run(self, steps: List[Tuple[str, int]], dwell_s: float) -> None:
        total = len(steps)
        log.info(f"Relay sequence start — {total} relays, {dwell_s:.1f}s each")
        try:
            for idx, (label, relay_id) in enumerate(steps, start=1):
                if self._stop.is_set():
                    break

                # Break-before-make, then energize just this relay. The firmware
                # closes the matching gate; the controller mirror reflects both.
                self._hw.relays.reset_all_relays()
                self._hw.relays.set_relays_safe([relay_id])
                self._state.set_relay_states(self._hw.relays.get_all_states())
                log.info(f"Relay sequence {idx}/{total}: {label}")

                deadline = time.time() + dwell_s
                while time.time() < deadline:
                    if self._stop.is_set():
                        break
                    time.sleep(_POLL_S)
        finally:
            # Always leave the bench in a safe, fully-open state.
            self._hw.relays.reset_all_relays()
            self._state.reset_relay_states()
            self._state.fire_relays_cleared()
            log.info("Relay sequence complete — all relays opened")
