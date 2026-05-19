"""
Measurement Matrix Engine.

Implements "one energised winding → validate ALL measurable outputs."

When auto_matrix.enabled is True the SequenceManager calls this engine
instead of the explicit 'tests' array.  The engine:

  1. Identifies the energise winding/tap from auto_matrix config
     (energisation is EXTERNAL — mains power applied by the operator;
      the relay board only routes the voltmeter probes)
  2. Enumerates EVERY measurable point in the transformer topology:
       • Full windings (non-energise) that have relay_a AND relay_b set
       • Taps on any winding that have at least one of relay_a/relay_b set
         (energise tap itself is skipped)
  3. For each point, asks RoutingEngine to resolve the relay set:
       [RL_a, RL_b, RL33, RL34]
  4. Emits one ExecutableStep per measurement point

No code changes are needed when new transformers or winding topologies are
added — everything derives from the relay_a/relay_b/tap relay fields in JSON.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.config_loader import AutoMatrixConfig, TransformerConfig, WindingConfig
from core.sequence_manager import ExecutableStep
from hardware.routing_engine import RoutingEngine, RoutingError

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class MeasurementPoint:
    """One measurable point identified from the transformer topology."""
    winding_id:       str
    tap_index:        Optional[int]    # None = full winding measurement
    expected_voltage: float
    relay_ids:        List[int]        # relays to close (from RoutingEngine)
    meas_channel:     int              # legacy ADC channel (kept for compatibility)
    label:            str


# ─────────────────────────────────────────────────────────────────────────────
class MeasurementMatrixEngine:
    """
    Builds the complete validation sequence from transformer topology.

    Public API
    ----------
    build_matrix(config) -> List[ExecutableStep]
    """

    def __init__(self) -> None:
        self._router = RoutingEngine()

    def build_matrix(self, config: TransformerConfig) -> List[ExecutableStep]:
        am = config.auto_matrix
        winding_map: Dict[str, WindingConfig] = {
            w.id: w for w in config.primary + config.secondary
        }

        energize_w = winding_map.get(am.energize_winding)
        if not energize_w:
            raise ValueError(
                f"Auto-matrix energise winding '{am.energize_winding}' not found "
                f"in config '{config.name}'"
            )

        points = self._enumerate_points(config, am)

        steps: List[ExecutableStep] = []
        for i, pt in enumerate(points):
            relay_map = {r: True for r in pt.relay_ids}
            steps.append(ExecutableStep(
                index=i,
                description=pt.label,
                from_winding_id=am.energize_winding,
                to_winding_id=pt.winding_id,
                expected_voltage=pt.expected_voltage,
                tolerance_percent=5.0,
                measurement_channel=pt.meas_channel,
                stabilization_delay_ms=500,
                relays_to_close=list(pt.relay_ids),
                relays_to_open=[],
                relay_map=relay_map,
                from_tap_index=am.energize_tap_index,
                to_tap_index=pt.tap_index,
            ))

        return steps

    # ── helpers ───────────────────────────────────────────────────────────────

    def _enumerate_points(
        self,
        config: TransformerConfig,
        am: AutoMatrixConfig,
    ) -> List[MeasurementPoint]:
        """
        Walk every winding and tap, collect all routable measurement points.
        The energise tap itself is skipped; all other points are included.
        """
        points: List[MeasurementPoint] = []

        for winding in config.primary + config.secondary:
            is_energize = (winding.id == am.energize_winding)

            # ── full winding (skip the energised winding itself) ──────────
            if not is_energize:
                relay_ids = self._route_winding(winding)
                if relay_ids is not None:
                    points.append(MeasurementPoint(
                        winding_id=winding.id,
                        tap_index=None,
                        expected_voltage=winding.voltage,
                        relay_ids=relay_ids,
                        meas_channel=max(winding.meas_channel, 0),
                        label=f"{winding.id}  {winding.voltage}V",
                    ))

            # ── taps (all windings, skip energise tap) ────────────────────
            for i, tap in enumerate(winding.taps):
                if is_energize and i == am.energize_tap_index:
                    continue   # skip the tap that carries mains

                relay_ids = self._route_tap(winding, tap)
                if relay_ids is None:
                    continue

                label   = tap.get("label", f"Tap {i}")
                voltage = float(tap.get("voltage", 0.0))
                mc      = int(tap.get("meas_channel", 0))
                points.append(MeasurementPoint(
                    winding_id=winding.id,
                    tap_index=i,
                    expected_voltage=voltage,
                    relay_ids=relay_ids,
                    meas_channel=mc,
                    label=f"{winding.id} @ {label}  ({voltage}V)",
                ))

        return points

    def _route_winding(self, winding: WindingConfig) -> Optional[List[int]]:
        try:
            return self._router.resolve_winding(winding)
        except RoutingError as exc:
            log.debug(f"Skipping winding '{winding.id}' — no route: {exc}")
            return None

    def _route_tap(self, winding: WindingConfig, tap: dict) -> Optional[List[int]]:
        try:
            return self._router.resolve_tap_auto(winding, tap)
        except RoutingError as exc:
            log.debug(
                f"Skipping tap '{tap.get('label','?')}' on '{winding.id}' — no route: {exc}"
            )
            return None
