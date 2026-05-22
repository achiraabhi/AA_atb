"""
TransformerRatioEngine — reduced-voltage no-load ratio validation.

Production testing applies a REDUCED excitation voltage to one winding.
All output voltages scale proportionally by the same ratio factor.

  ratio_factor = applied_voltage / excitation_nominal_voltage
  expected_out = nominal_output_voltage * ratio_factor

Validation uses a HYBRID tolerance: percentage-based AND a minimum absolute
delta, so small adjacent voltages (e.g. 11V vs 13V windings) remain
distinguishable even at relaxed percentage tolerances.
"""
from typing import Dict, List, Optional, Tuple


class TransformerRatioEngine:
    """
    Stateless calculation engine.  All methods are pure functions.
    """

    # ── ratio computation ─────────────────────────────────────────────────────

    @staticmethod
    def compute_ratio(applied_voltage: float,
                      excitation_nominal: float) -> float:
        """k = V_applied / V_nominal_excitation."""
        if excitation_nominal == 0:
            raise ValueError("Excitation nominal voltage cannot be zero")
        return applied_voltage / excitation_nominal

    @staticmethod
    def compute_expected(nominal_output: float, ratio: float) -> float:
        """V_expected = V_nominal_output * k."""
        return nominal_output * ratio

    # ── measurement validation ─────────────────────────────────────────────────

    @staticmethod
    def validate_measurement(measured: float,
                              expected: float,
                              tolerance_pct: float,
                              min_abs_delta: float = 0.1) -> Tuple[bool, str]:
        """
        Hybrid tolerance check.
        Passes when |measured - expected| <= max(expected * tol%, min_abs_delta).
        Returns (passed, reason_string).
        """
        deviation = abs(measured - expected)
        pct_window = expected * (tolerance_pct / 100.0)
        window = max(pct_window, min_abs_delta)

        if deviation <= window:
            return True, "PASS"
        dev_pct = (deviation / expected * 100.0) if expected else float('inf')
        return False, (
            f"FAIL: measured={measured:.4f}V expected={expected:.4f}V "
            f"deviation={dev_pct:.2f}% (window=±{window:.4f}V)"
        )

    # ── relative ordering validation ──────────────────────────────────────────

    @staticmethod
    def validate_ordering(winding_nominal_to_measured: Dict[float, float]) -> Tuple[bool, List[str]]:
        """
        Validate that measured voltages preserve the correct relative ordering
        of the nominal voltages.  e.g. 11V winding < 13V winding < 18V winding.

        Args:
            winding_nominal_to_measured: dict of {nominal_voltage: measured_voltage}

        Returns:
            (all_ok, list_of_violation_strings)
        """
        sorted_by_nominal = sorted(winding_nominal_to_measured.items())
        violations = []

        for i in range(len(sorted_by_nominal) - 1):
            nom_lo, meas_lo = sorted_by_nominal[i]
            nom_hi, meas_hi = sorted_by_nominal[i + 1]
            if nom_lo == nom_hi:
                continue  # same nominal — skip ordering check
            if meas_lo >= meas_hi:
                violations.append(
                    f"Ordering violation: winding@{nom_lo}V measured {meas_lo:.3f}V "
                    f">= winding@{nom_hi}V measured {meas_hi:.3f}V"
                )

        return len(violations) == 0, violations

    # ── per-winding expected voltage table ────────────────────────────────────

    @classmethod
    def compute_all_expected(cls,
                              excitation_nominal: float,
                              applied_voltage: float,
                              winding_nominals: Dict[str, float]
                              ) -> Dict[str, float]:
        """
        Given a dict of {winding_id: nominal_voltage}, return
        {winding_id: expected_measured_voltage} at the given applied excitation.
        """
        ratio = cls.compute_ratio(applied_voltage, excitation_nominal)
        return {wid: cls.compute_expected(nom, ratio) for wid, nom in winding_nominals.items()}
