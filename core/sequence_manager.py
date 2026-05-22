"""
Sequence manager — translates transformer test definitions into ordered,
relay-mapped test steps ready for execution by the test engine.

Step types:
  is_ratio_step=False  — legacy steps with a fixed expected_voltage
  is_ratio_step=True   — segment-pair ratio steps: each step closes TWO relays
                         for the excitation segment AND TWO relays for the
                         measurement segment (four relays total).
                         Expected voltage is computed at runtime using:
                           ratio = applied_voltage / exc_seg.nominal_voltage
                           expected = meas_seg.nominal_voltage * ratio
"""
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from core.config_loader import (
    TransformerConfig, TestStep, WindingConfig,
    RatioValidationRule, SegmentSpec,
)


@dataclass
class ExecutableStep:
    """A single actionable test step with all relay assignments resolved."""
    index:                  int
    description:            str
    from_winding_id:        str
    to_winding_id:          str
    expected_voltage:       float          # 0.0 for ratio steps
    tolerance_percent:      float
    measurement_channel:    int
    stabilization_delay_ms: int
    relays_to_close:        List[int] = field(default_factory=list)
    relays_to_open:         List[int] = field(default_factory=list)
    relay_map:              Dict[int, bool] = field(default_factory=dict)
    from_tap_index:         Optional[int] = None
    to_tap_index:           Optional[int] = None
    # ── ratio step fields ─────────────────────────────────────────────────
    is_ratio_step:          bool  = False
    nominal_input_voltage:  float = 0.0   # excitation segment nominal (V)
    nominal_output_voltage: float = 0.0   # measurement segment nominal (V)
    minimum_absolute_delta: float = 0.1   # hybrid tolerance floor (V)
    critical:               bool  = True


class SequenceManager:
    """
    Builds an ordered list of ExecutableSteps from a TransformerConfig.

    Priority:
      1. ratio_rules  (dynamic ratio-based, preferred)
      2. auto_matrix  (auto-generated sweep via MeasurementMatrixEngine)
      3. tests array  (explicit legacy steps)
    """

    def __init__(self):
        self._steps: List[ExecutableStep] = []
        self._current_index: int = -1
        self._transformer_config: Optional[TransformerConfig] = None

    # ── Public API ────────────────────────────────────────────────────────

    def load(self, config: TransformerConfig) -> List[ExecutableStep]:
        self._transformer_config = config
        self._steps = []
        self._current_index = -1

        enabled_rules = [r for r in config.ratio_rules if r.enabled]
        if enabled_rules:
            winding_map = config.winding_map
            for idx, rule in enumerate(enabled_rules):
                self._steps.append(self._resolve_ratio_rule(idx, rule, winding_map))
        elif config.auto_matrix.enabled:
            from core.measurement_matrix_engine import MeasurementMatrixEngine
            self._steps = MeasurementMatrixEngine().build_matrix(config)
        else:
            winding_map = config.winding_map
            for idx, test in enumerate(config.tests):
                self._steps.append(self._resolve_step(idx, test, winding_map))

        return list(self._steps)

    def reset(self) -> None:
        self._current_index = -1

    @property
    def steps(self) -> List[ExecutableStep]:
        return list(self._steps)

    @property
    def total_steps(self) -> int:
        return len(self._steps)

    @property
    def current_index(self) -> int:
        return self._current_index

    def has_next(self) -> bool:
        return self._current_index < len(self._steps) - 1

    def advance(self) -> Optional[ExecutableStep]:
        if not self.has_next():
            return None
        self._current_index += 1
        return self._steps[self._current_index]

    def current_step(self) -> Optional[ExecutableStep]:
        if self._current_index < 0 or self._current_index >= len(self._steps):
            return None
        return self._steps[self._current_index]

    def get_step(self, index: int) -> Optional[ExecutableStep]:
        if 0 <= index < len(self._steps):
            return self._steps[index]
        return None

    def seek(self, index: int) -> bool:
        if 0 <= index < len(self._steps):
            self._current_index = index - 1
            return True
        return False

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _parse_node_id(node_id: str) -> Tuple[str, Optional[int], bool]:
        """
        Parse a node ID string.

        Returns (winding_id, tap_index_or_None, is_end_node).

        "P1"      → ("P1", None,  False)   — winding start node
        "P1:end"  → ("P1", None,  True )   — winding end node
        "P1:tap0" → ("P1", 0,     False)   — tap node
        """
        if ":" not in node_id:
            return node_id, None, False
        wind_id, suffix = node_id.split(":", 1)
        if suffix == "end":
            return wind_id, None, True
        if suffix.startswith("tap"):
            try:
                return wind_id, int(suffix[3:]), False
            except ValueError:
                return wind_id, None, False
        return wind_id, None, False

    @classmethod
    def _node_relay(cls, node_id: str,
                    winding_map: Dict[str, WindingConfig]) -> Optional[int]:
        """
        Resolve a node ID to the relay number that connects it to the test bus.

        Start node  ("P1")      → winding.relay_a
        End node    ("P1:end")  → winding.relay_b
        Tap node    ("P1:tap0") → tap dict relay_a or relay_b
        """
        wind_id, tap_idx, is_end = cls._parse_node_id(node_id)
        w = winding_map.get(wind_id)
        if w is None:
            return None
        if tap_idx is not None:
            if tap_idx < len(w.taps):
                tap = w.taps[tap_idx]
                return (tap.get("relay_a") if tap.get("relay_a") is not None
                        else tap.get("relay_b"))
            return None
        if is_end:
            return w.relay_b if w.relay_b is not None else w.end_relay
        return w.relay_a if w.relay_a is not None else w.relay_id

    @classmethod
    def _segment_relays(cls, seg: SegmentSpec,
                        winding_map: Dict[str, WindingConfig]) -> List[int]:
        """Return the (up to 2) relay numbers needed to switch a segment."""
        relays = []
        for node_id in (seg.node_a, seg.node_b):
            r = cls._node_relay(node_id, winding_map)
            if r is not None and r not in relays:
                relays.append(r)
        return relays

    @classmethod
    def _resolve_ratio_rule(cls, idx: int, rule: RatioValidationRule,
                             winding_map: Dict[str, WindingConfig]) -> ExecutableStep:
        """
        Build an ExecutableStep from a RatioValidationRule.

        Closes up to 4 relays:
          excitation_segment.node_a relay
          excitation_segment.node_b relay
          measurement_segment.node_a relay
          measurement_segment.node_b relay
        """
        exc  = rule.excitation_segment
        meas = rule.measurement_segment

        relay_map: Dict[int, bool] = {}
        for rid in cls._segment_relays(exc,  winding_map):
            relay_map[rid] = True
        for rid in cls._segment_relays(meas, winding_map):
            relay_map[rid] = True

        # Infer nominal voltages from winding config when not set in rule
        nom_in  = exc.nominal_voltage
        nom_out = meas.nominal_voltage
        exc_wind_id,  _, _ = cls._parse_node_id(exc.node_a)
        meas_wind_id, _, _ = cls._parse_node_id(meas.node_a)
        exc_w  = winding_map.get(exc_wind_id)
        meas_w = winding_map.get(meas_wind_id)
        if nom_in  == 0.0 and exc_w:
            nom_in  = exc_w.voltage
        if nom_out == 0.0 and meas_w:
            nom_out = meas_w.voltage

        crit_str = "CRITICAL " if rule.critical else ""
        desc = (f"{crit_str}EXC[{exc.node_a}<>{exc.node_b}] "
                f"MEAS[{meas.node_a}<>{meas.node_b}]  "
                f"{nom_out:.0f}V/{nom_in:.0f}V nominal  "
                f"+-{rule.tolerance_percent}%")

        # Preserve tap index hints for test_engine (from excitation side)
        _, exc_tap, _  = cls._parse_node_id(exc.node_a)
        _, meas_tap, _ = cls._parse_node_id(meas.node_a)

        return ExecutableStep(
            index=idx,
            description=desc,
            from_winding_id=exc_wind_id,
            to_winding_id=meas_wind_id,
            expected_voltage=0.0,          # computed at runtime from ratio
            tolerance_percent=rule.tolerance_percent,
            measurement_channel=0,
            stabilization_delay_ms=500,
            relays_to_close=[rid for rid, st in relay_map.items() if st],
            relays_to_open=[rid for rid, st in relay_map.items() if not st],
            relay_map=relay_map,
            from_tap_index=exc_tap,
            to_tap_index=meas_tap,
            is_ratio_step=True,
            nominal_input_voltage=nom_in,
            nominal_output_voltage=nom_out,
            minimum_absolute_delta=rule.minimum_absolute_delta,
            critical=rule.critical,
        )

    @staticmethod
    def _resolve_step(idx: int, test: TestStep,
                      winding_map: Dict[str, WindingConfig]) -> ExecutableStep:
        """Resolve relay assignments for a legacy TestStep."""
        from_w = winding_map.get(test.from_winding)
        to_w   = winding_map.get(test.to_winding)

        relay_map: Dict[int, bool] = dict(test.relay_map) if test.relay_map else {}

        if from_w and from_w.relay_id is not None and from_w.relay_id not in relay_map:
            relay_map[from_w.relay_id] = True
        if to_w and to_w.relay_id is not None and to_w.relay_id not in relay_map:
            relay_map[to_w.relay_id] = True

        desc = test.description or f"Test {test.from_winding} → {test.to_winding}"

        return ExecutableStep(
            index=idx,
            description=desc,
            from_winding_id=test.from_winding,
            to_winding_id=test.to_winding,
            expected_voltage=test.expected_voltage,
            tolerance_percent=test.tolerance_percent,
            measurement_channel=test.measurement_channel,
            stabilization_delay_ms=test.stabilization_delay_ms,
            relays_to_close=[rid for rid, st in relay_map.items() if st],
            relays_to_open=[rid for rid, st in relay_map.items() if not st],
            relay_map=relay_map,
            from_tap_index=test.from_tap_index,
            to_tap_index=test.to_tap_index,
            is_ratio_step=False,
        )
