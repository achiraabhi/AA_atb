"""
Measurement Matrix Engine.

Auto-generates the full validation sequence from transformer topology.

Step ordering (the "energise-first" rule):
  Phase 1 — Energised winding's own taps
              Every tap on the energised winding is measured FIRST
              (except the tap that is carrying mains, which is skipped).
              Verifies the winding's internal tap ratios.

  Phase 2 — Same-side windings (primary or secondary, same group as the
              energised winding, excluding the energised winding itself).
              For each: full winding voltage, then each tap.

  Phase 3 — Other-side windings (the opposite group).
              For each: full winding voltage, then each tap.

All generated steps are ratio steps (is_ratio_step=True) so the expected
voltage scales automatically with whatever excitation voltage is applied,
without needing to know the exact mains voltage in advance.

The energised winding is determined at test-start time from the operator's
selection (passed in via SequenceManager.load), overriding whatever
auto_matrix.energize_winding says in the config JSON.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.config_loader import TransformerConfig, WindingConfig
from core.sequence_manager import ExecutableStep
from hardware.routing_engine import RoutingEngine, RoutingError

log = logging.getLogger(__name__)


@dataclass
class MeasurementPoint:
    """One measurable point identified from the transformer topology."""
    winding_id:          str
    tap_index:           Optional[int]   # None = full winding
    nominal_voltage:     float           # nominal voltage at this point
    relay_ids:           List[int]
    meas_channel:        int
    label:               str
    phase:               str             # "own_tap" | "same_side" | "other_side"


class MeasurementMatrixEngine:
    """
    Builds the ordered validation sequence from transformer topology.

    Public API
    ----------
    build_matrix(config, excitation_winding_id, energize_tap_index) -> List[ExecutableStep]
    """

    def __init__(self) -> None:
        self._router = RoutingEngine()

    def build_matrix(
        self,
        config: TransformerConfig,
        excitation_winding_id: Optional[str] = None,
        energize_tap_index: Optional[int] = None,
    ) -> List[ExecutableStep]:
        am = config.auto_matrix

        # Runtime selection overrides config
        ew_id  = excitation_winding_id or am.energize_winding
        et_idx = energize_tap_index if energize_tap_index is not None else am.energize_tap_index

        if not ew_id:
            raise ValueError(
                f"No energise winding specified for '{config.name}'. "
                "Set auto_matrix.energize_winding in the config or select "
                "an excitation winding before starting the test."
            )

        winding_map: Dict[str, WindingConfig] = {
            w.id: w for w in config.primary + config.secondary
        }
        energize_w = winding_map.get(ew_id)
        if not energize_w:
            raise ValueError(
                f"Energise winding '{ew_id}' not found in config '{config.name}'"
            )

        # Nominal excitation voltage: tap voltage if energising at a tap, else full winding
        if et_idx is not None and et_idx < len(energize_w.taps):
            nom_exc = float(energize_w.taps[et_idx].get("voltage", energize_w.voltage))
        else:
            nom_exc = energize_w.voltage

        points = self._enumerate_points(config, energize_w, ew_id, et_idx)

        steps: List[ExecutableStep] = []
        for i, pt in enumerate(points):
            relay_map = {r: True for r in pt.relay_ids}
            steps.append(ExecutableStep(
                index=i,
                description=pt.label,
                from_winding_id=ew_id,
                to_winding_id=pt.winding_id,
                expected_voltage=0.0,               # computed at runtime via ratio
                tolerance_percent=5.0,
                measurement_channel=pt.meas_channel,
                stabilization_delay_ms=500,
                relays_to_close=list(pt.relay_ids),
                relays_to_open=[],
                relay_map=relay_map,
                from_tap_index=et_idx,
                to_tap_index=pt.tap_index,
                is_ratio_step=True,
                nominal_input_voltage=nom_exc,
                nominal_output_voltage=pt.nominal_voltage,
                minimum_absolute_delta=0.1,
                critical=True,
            ))

        log.info(
            f"[MatrixEngine] {config.name}: energise={ew_id}"
            + (f" tap{et_idx}" if et_idx is not None else "")
            + f"  nom_exc={nom_exc}V  → {len(steps)} steps"
        )
        return steps

    # ── ordering ──────────────────────────────────────────────────────────────

    def _enumerate_points(
        self,
        config: TransformerConfig,
        energize_w: WindingConfig,
        ew_id: str,
        et_idx: Optional[int],
    ) -> List[MeasurementPoint]:
        """
        Build the ordered list of measurement points:
          Phase 1 — energised winding's own taps (except the energise tap)
          Phase 2 — same-side other windings: full then taps each
          Phase 3 — other-side windings:      full then taps each
        """
        # Determine same-side and other-side winding lists
        primary_ids = {w.id for w in config.primary}
        if ew_id in primary_ids:
            same_side = [w for w in config.primary  if w.id != ew_id]
            other_side = list(config.secondary)
        else:
            same_side = [w for w in config.secondary if w.id != ew_id]
            other_side = list(config.primary)

        points: List[MeasurementPoint] = []

        # ── Phase 1: energised winding's own taps ─────────────────────────
        for i, tap in enumerate(energize_w.taps):
            if i == et_idx:
                continue                 # skip the tap carrying mains
            # Every tap on the winding gets a measurement point — the operator
            # fills in the voltage (and relays) on the generated rule afterward.
            # To exclude a tap, delete it from the winding in the editor.
            # Routing is best-effort: relays may be unassigned at design time
            # (the operator fills relay numbers in later). Generate the point
            # regardless so the measurement sequence is complete.
            #
            # This IS the energizing winding: its main wires are external and its
            # start is hard-wired to the + bus, so the tap routes on its B relay
            # alone (no A relay).
            relay_ids = self._route_tap(energize_w, tap, energizing=True) or []
            label   = tap.get("label", f"Tap {i}")
            voltage = float(tap.get("voltage", 0.0))
            mc      = int(tap.get("meas_channel", 0))
            points.append(MeasurementPoint(
                winding_id=ew_id,
                tap_index=i,
                nominal_voltage=voltage,
                relay_ids=relay_ids,
                meas_channel=mc,
                label=f"{ew_id} own tap: {label}  ({voltage}V)",
                phase="own_tap",
            ))

        # ── Phase 2: same-side windings ───────────────────────────────────
        for winding in same_side:
            self._add_winding_points(points, winding, phase="same_side")

        # ── Phase 3: other-side windings ──────────────────────────────────
        for winding in other_side:
            self._add_winding_points(points, winding, phase="other_side")

        return points

    def _add_winding_points(
        self,
        points: List[MeasurementPoint],
        winding: WindingConfig,
        phase: str,
    ) -> None:
        """Append full-winding point then tap points for a single winding.

        Routing is best-effort: relays may be unassigned at design time, so
        points are always generated (with empty relay lists if unresolved).
        """
        # Full winding
        relay_ids = self._route_winding(winding) or []
        points.append(MeasurementPoint(
            winding_id=winding.id,
            tap_index=None,
            nominal_voltage=winding.voltage,
            relay_ids=relay_ids,
            meas_channel=max(winding.meas_channel, 0),
            label=f"{winding.id}  {winding.voltage}V",
            phase=phase,
        ))

        # Taps — every tap gets a point (operator fills voltage/relays later)
        for i, tap in enumerate(winding.taps):
            relay_ids = self._route_tap(winding, tap) or []
            label   = tap.get("label", f"Tap {i}")
            voltage = float(tap.get("voltage", 0.0))
            mc      = int(tap.get("meas_channel", 0))
            points.append(MeasurementPoint(
                winding_id=winding.id,
                tap_index=i,
                nominal_voltage=voltage,
                relay_ids=relay_ids,
                meas_channel=mc,
                label=f"{winding.id} @ {label}  ({voltage}V)",
                phase=phase,
            ))

    # ── routing helpers ───────────────────────────────────────────────────────

    def _route_winding(self, winding: WindingConfig) -> Optional[List[int]]:
        try:
            return self._router.resolve_winding(winding)
        except RoutingError as exc:
            log.debug(f"Skipping winding '{winding.id}' — no route: {exc}")
            return None

    def _route_tap(self, winding: WindingConfig, tap: dict,
                   energizing: bool = False) -> Optional[List[int]]:
        try:
            return self._router.resolve_tap_auto(winding, tap, energizing=energizing)
        except RoutingError as exc:
            log.debug(
                f"Skipping tap '{tap.get('label', '?')}' on '{winding.id}' — no route: {exc}"
            )
            return None
