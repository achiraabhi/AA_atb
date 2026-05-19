"""
JSON-based transformer configuration loader.
Discovers, validates, and caches transformer definitions.

Relay assignment schema (updated):
  Winding:
    relay_a : int | null  — RL1-16 relay connected to start_pin  (voltmeter + probe)
    relay_b : int | null  — RL17-32 relay connected to end_pin   (voltmeter − probe)

  Tap dict:
    relay_a : int | null  — RL1-16 relay for tap_pin  (when tap is the + probe)
    relay_b : int | null  — RL17-32 relay for tap_pin (when tap is the − probe)

  Backward compatibility:
    Old field relay_id  → used as relay_a if relay_a is absent
    Old field end_relay → used as relay_b if relay_b is absent
    Old tap field relay_channel → used as relay_b if neither relay_a/relay_b set
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class WindingConfig:
    id:           str
    start_pin:    int
    end_pin:      int
    voltage:      float
    dot_polarity: bool = True
    taps:         List[Dict] = field(default_factory=list)
    coords:       Dict = field(default_factory=dict)
    relay_a:      Optional[int] = None  # RL1-16  start_pin → voltmeter +
    relay_b:      Optional[int] = None  # RL17-32 end_pin   → voltmeter −
    meas_channel: int = -1              # legacy ADC channel (-1 = unused)
    # ── deprecated fields kept for backward compat ──────────────────────
    relay_id:     Optional[int] = None  # old: same as relay_a
    end_relay:    Optional[int] = None  # old: same as relay_b


@dataclass
class AutoMatrixConfig:
    """
    When enabled, SequenceManager ignores the explicit 'tests' array and
    auto-generates the full validation sweep via MeasurementMatrixEngine.
    """
    enabled:           bool = False
    energize_winding:  str  = ""
    energize_tap_index: Optional[int] = None   # None = full winding energised


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
    tests:            List[TestStep]
    rated_power_va:   float = 0.0
    rated_frequency_hz: float = 50.0
    notes:            str = ""
    file_path:        str = ""
    auto_matrix:      AutoMatrixConfig = field(default_factory=AutoMatrixConfig)


class ConfigLoader:
    """
    Scans the transformers/ directory and loads all JSON configs.
    Provides validation and caching.
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

        primary     = [self._parse_winding(w) for w in raw["primary"]]
        secondary   = [self._parse_winding(w) for w in raw["secondary"]]
        tests       = [self._parse_test(t) for t in raw["tests"]]
        auto_matrix = self._parse_auto_matrix(raw.get("auto_matrix", {}))

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
        )

    @staticmethod
    def _parse_winding(w: Dict) -> WindingConfig:
        # Prefer new relay_a / relay_b; fall back to legacy relay_id / end_relay
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
    def _slugify(name: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in name).lower().strip("_")
