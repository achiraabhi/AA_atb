"""
JSON-based transformer configuration loader.
Discovers, validates, and caches transformer definitions.

Relay assignment schema:
  Winding:
    relay_a : int | null  — RL1-16  connected to start_pin  (voltmeter + probe)
    relay_b : int | null  — RL17-32 connected to end_pin    (voltmeter − probe)

  Tap dict:
    relay_b : int | null  — RL17-32 single relay for tap_pin (− probe).
                            Measured start→tap = winding.relay_a + tap.relay_b.
                            (Legacy relay_a on taps is ignored.)

  Backward compatibility:
    Old field relay_id  → used as relay_a if relay_a is absent
    Old field end_relay → used as relay_b if relay_b is absent
    Old tap field relay_channel → used as relay_b if relay_b is absent

Electrical segment model:
  A validation rule now operates on two SEGMENTS, not single nodes.
  A segment is a voltage that exists between two electrical nodes (node_a ↔ node_b).

  Node ID format:
    "W1"       — start node of winding W1 (0 V reference end)
    "W1:end"   — end node of winding W1
    "W1:tap0"  — tap index 0 of winding W1

  Segment examples:
    P1 ↔ P1:end       — full primary winding (600 V)
    P1 ↔ P1:tap2      — partial winding to tap (230 V)
    P1:tap1 ↔ P1:tap2 — between two taps (10 V)

  Backward compat:
    Old format (from_node / to_node) is auto-migrated to segment format on load.
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field


@dataclass
class WindingConfig:
    id:           str
    # A pin IS its relay number (one number per wire). The energizing winding's
    # two mains wires carry no relay, so they are labelled "EN+" / "EN-" instead.
    start_pin:    Union[int, str]
    end_pin:      Union[int, str]
    voltage:      float           # nominal voltage at full excitation
    dot_polarity: bool = True
    taps:         List[Dict] = field(default_factory=list)
    coords:       Dict = field(default_factory=dict)
    relay_a:      Optional[int] = None   # 1-16  start_pin → voltmeter +
    relay_b:      Optional[int] = None   # 17-32 end_pin   → voltmeter −
    meas_channel: int = -1
    can_energize: bool = True            # may be used as excitation winding
    # ── deprecated fields kept for backward compat ──────────────────────
    relay_id:     Optional[int] = None
    end_relay:    Optional[int] = None

    @property
    def nominal_voltage(self) -> float:
        """Alias for voltage — preferred name in ratio calculations."""
        return self.voltage


@dataclass
class SegmentSpec:
    """
    An electrical segment: a voltage that exists between two nodes.

    node_a and node_b are the two endpoints.  The segment's nominal_voltage
    is the voltage measured across node_a ↔ node_b at full (rated) excitation.
    """
    node_a:          str
    node_b:          str
    nominal_voltage: float = 0.0


@dataclass
class RatioValidationRule:
    """
    Segment-pair ratio validation rule.

    The measurement validates:
        measured_voltage / applied_excitation == nominal_meas / nominal_exc

    excitation_segment : where the reduced test voltage is APPLIED
    measurement_segment: where the induced voltage is MEASURED

    Node ID format: "W1" = winding start, "W1:end" = winding end,
                    "W1:tap0" = tap index 0.
    """
    id:                       str
    excitation_segment:       SegmentSpec
    measurement_segment:      SegmentSpec
    tolerance_percent:        float = 10.0
    minimum_absolute_delta:   float = 0.1    # hybrid tolerance floor (V)
    measurement_type:         str   = "AC"
    critical:                 bool  = True
    enabled:                  bool  = True

    # ── backward-compat properties (used by test_engine / state_manager) ──
    @property
    def from_node(self) -> str:
        return self.excitation_segment.node_a

    @property
    def to_node(self) -> str:
        return self.measurement_segment.node_a

    @property
    def nominal_input_voltage(self) -> float:
        return self.excitation_segment.nominal_voltage

    @property
    def nominal_output_voltage(self) -> float:
        return self.measurement_segment.nominal_voltage


@dataclass
class AutoMatrixConfig:
    enabled:            bool = False
    energize_winding:   str  = ""
    energize_tap_index: Optional[int] = None


@dataclass
class TestStep:
    from_winding:           str
    to_winding:             str
    expected_voltage:       float
    tolerance_percent:      float = 5.0
    measurement_channel:    int   = 0
    stabilization_delay_ms: int   = 500
    relay_map:              Dict[int, bool] = field(default_factory=dict)
    description:            str   = ""
    from_tap_index:         Optional[int] = None
    to_tap_index:           Optional[int] = None


@dataclass
class TransformerConfig:
    name:             str
    transformer_id:   str
    transformer_type: str
    primary:          List[WindingConfig]
    secondary:        List[WindingConfig]
    tests:              List[TestStep]
    rated_power_va:     float = 0.0
    rated_frequency_hz: float = 50.0
    notes:              str = ""
    file_path:          str = ""
    auto_matrix:        AutoMatrixConfig = field(default_factory=AutoMatrixConfig)
    ratio_rules:        List[RatioValidationRule] = field(default_factory=list)

    @property
    def windings(self) -> List[WindingConfig]:
        """Flat list of all windings — primary and secondary are equivalent."""
        return self.primary + self.secondary

    @property
    def winding_map(self) -> Dict[str, WindingConfig]:
        return {w.id: w for w in self.windings}

    def get_winding(self, winding_id: str) -> Optional[WindingConfig]:
        return self.winding_map.get(winding_id)

    def energizable_windings(self) -> List[WindingConfig]:
        return [w for w in self.windings if w.can_energize]


class ConfigLoader:
    """
    Scans the transformers/ directory and loads all JSON configs.
    """

    REQUIRED_TOP_KEYS = {"name", "primary", "secondary", "tests"}

    def __init__(self, transformers_dir: Optional[str] = None) -> None:
        if transformers_dir is None:
            base = Path(__file__).parent.parent
            transformers_dir = str(base / "transformers")
        self._dir   = Path(transformers_dir)
        self._cache: Dict[str, TransformerConfig] = {}

    # ── public API ────────────────────────────────────────────────────────

    def scan_and_load(self) -> List[str]:
        self._cache.clear()
        if not self._dir.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            return []
        loaded = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                cfg = self._load_file(path)
                self._cache[cfg.transformer_id] = cfg
                loaded.append(cfg.transformer_id)
            except Exception as exc:
                print(f"[ConfigLoader] Skipping {path.name}: {exc}")
        return loaded

    def get_transformer(self, transformer_id: str) -> Optional[TransformerConfig]:
        return self._cache.get(transformer_id)

    def list_transformers(self) -> List[TransformerConfig]:
        return list(self._cache.values())

    def list_names(self) -> List[str]:
        return [cfg.name for cfg in self._cache.values()]

    def save_transformer(self, data: Dict, filename: Optional[str] = None) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        if filename is None:
            tid  = data.get("transformer_id", data.get("name", "unknown"))
            safe = "".join(c if c.isalnum() else "_" for c in tid).lower()
            filename = f"{safe}.json"
        path = self._dir / filename
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        cfg = self._load_file(path)
        self._cache[cfg.transformer_id] = cfg
        return str(path)

    def load_from_file(self, path: str) -> TransformerConfig:
        cfg = self._load_file(Path(path))
        self._cache[cfg.transformer_id] = cfg
        return cfg

    def get_raw_json(self, transformer_id: str) -> Optional[Dict]:
        cfg = self._cache.get(transformer_id)
        if cfg is None:
            return None
        with open(cfg.file_path) as f:
            return json.load(f)

    # ── internal helpers ──────────────────────────────────────────────────

    def _load_file(self, path: Path) -> TransformerConfig:
        with open(path) as f:
            raw = json.load(f)
        self._validate(raw, path.name)
        return self._parse(raw, str(path))

    def _validate(self, raw: Dict, filename: str) -> None:
        missing = self.REQUIRED_TOP_KEYS - set(raw.keys())
        if missing:
            raise ValueError(f"Missing keys in {filename}: {missing}")
        if not raw["primary"]:
            raise ValueError(f"{filename}: 'primary' list is empty")
        if not raw["secondary"]:
            raise ValueError(f"{filename}: 'secondary' list is empty")

    def _parse(self, raw: Dict, file_path: str) -> TransformerConfig:
        tid = raw.get("transformer_id") or self._slugify(raw["name"])

        primary   = [self._parse_winding(w) for w in raw["primary"]]
        secondary = [self._parse_winding(w) for w in raw["secondary"]]
        tests     = [self._parse_test(t) for t in raw["tests"]]
        auto_matrix = self._parse_auto_matrix(raw.get("auto_matrix", {}))

        # Support ratio_rules (new) | validation_rules (legacy key)
        ratio_rules_raw = raw.get("ratio_rules", raw.get("validation_rules", []))
        ratio_rules = [self._parse_ratio_rule(r) for r in ratio_rules_raw]

        return TransformerConfig(
            name=raw["name"],
            transformer_id=tid,
            transformer_type=raw.get("type", "unknown"),
            primary=primary,
            secondary=secondary,
            tests=tests,
            rated_power_va=raw.get("rated_power_va", 0.0),
            rated_frequency_hz=raw.get("rated_frequency_hz", 50.0),
            notes=raw.get("notes", ""),
            file_path=file_path,
            auto_matrix=auto_matrix,
            ratio_rules=ratio_rules,
        )

    @staticmethod
    def _parse_winding(w: Dict) -> WindingConfig:
        relay_id_raw  = w.get("relay_id")
        end_relay_raw = w.get("end_relay")
        relay_a = w.get("relay_a", relay_id_raw)
        relay_b = w.get("relay_b", end_relay_raw)
        return WindingConfig(
            id=w["id"],
            start_pin=w["start_pin"],
            end_pin=w["end_pin"],
            voltage=w.get("voltage", 0.0),
            dot_polarity=w.get("dot_polarity", True),
            taps=w.get("taps", []),
            coords=w.get("coords", {}),
            relay_a=relay_a,
            relay_b=relay_b,
            meas_channel=w.get("meas_channel", -1),
            can_energize=bool(w.get("can_energize") if w.get("can_energize") is not None else True),
            relay_id=relay_id_raw,
            end_relay=end_relay_raw,
        )

    @staticmethod
    def _parse_auto_matrix(d: Dict) -> AutoMatrixConfig:
        return AutoMatrixConfig(
            enabled=bool(d.get("enabled", False)),
            energize_winding=d.get("energize_winding", ""),
            energize_tap_index=d.get("energize_tap_index"),
        )

    @staticmethod
    def _parse_test(t: Dict) -> TestStep:
        return TestStep(
            from_winding=t["from"],
            to_winding=t["to"],
            expected_voltage=t["expected_voltage"],
            tolerance_percent=t.get("tolerance_percent", 5.0),
            measurement_channel=t.get("measurement_channel", 0),
            stabilization_delay_ms=t.get("stabilization_delay_ms", 500),
            relay_map={int(k): v for k, v in t.get("relay_map", {}).items()},
            description=t.get("description", ""),
            from_tap_index=t.get("from_tap_index"),
            to_tap_index=t.get("to_tap_index"),
        )

    @staticmethod
    def _legacy_node_to_segment(node_id: str, nominal_voltage: float) -> SegmentSpec:
        """
        Migrate a legacy single-node ID to a SegmentSpec.

        "P1"       → { node_a: "P1",  node_b: "P1:end" }  (full winding)
        "P1:end"   → { node_a: "P1",  node_b: "P1:end" }  (full winding)
        "P1:tap0"  → { node_a: "P1",  node_b: "P1:tap0" } (start-to-tap segment)
        """
        if not node_id:
            return SegmentSpec(node_a="", node_b="", nominal_voltage=nominal_voltage)
        if ":" not in node_id or node_id.endswith(":end"):
            wind_id = node_id.split(":")[0]
            return SegmentSpec(node_a=wind_id, node_b=f"{wind_id}:end",
                               nominal_voltage=nominal_voltage)
        # tap node — segment runs from winding start to the tap
        wind_id = node_id.split(":")[0]
        return SegmentSpec(node_a=wind_id, node_b=node_id,
                           nominal_voltage=nominal_voltage)

    @classmethod
    def _parse_ratio_rule(cls, r: Dict) -> RatioValidationRule:
        rid = r.get("id", "")

        if "excitation_segment" in r:
            # New segment-based format
            exc_raw  = r["excitation_segment"]
            meas_raw = r["measurement_segment"]
            exc_seg  = SegmentSpec(
                node_a=exc_raw["node_a"],
                node_b=exc_raw["node_b"],
                nominal_voltage=float(exc_raw.get("nominal_voltage", 0.0)),
            )
            meas_seg = SegmentSpec(
                node_a=meas_raw["node_a"],
                node_b=meas_raw["node_b"],
                nominal_voltage=float(meas_raw.get("nominal_voltage", 0.0)),
            )
        else:
            # Legacy from_node / to_node — auto-migrate to segment format
            from_node = r.get("from_node", r.get("from_winding", ""))
            to_node   = r.get("to_node",   r.get("to_winding",   ""))
            nom_in    = float(r.get("nominal_input_voltage",  r.get("expected_voltage", 0.0)))
            nom_out   = float(r.get("nominal_output_voltage", 0.0))
            exc_seg   = cls._legacy_node_to_segment(from_node, nom_in)
            meas_seg  = cls._legacy_node_to_segment(to_node,   nom_out)

        return RatioValidationRule(
            id=rid,
            excitation_segment=exc_seg,
            measurement_segment=meas_seg,
            tolerance_percent=float(r.get("tolerance_percent", 10.0)),
            minimum_absolute_delta=float(r.get("minimum_absolute_delta", 0.1)),
            measurement_type=r.get("measurement_type", "AC"),
            critical=bool(r.get("critical", True)),
            enabled=bool(r.get("enabled", True)),
        )

    @staticmethod
    def _slugify(name: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in name).lower().strip("_")
