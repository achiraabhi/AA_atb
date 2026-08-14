"""
Transformer configuration validator.
Returns structured list of errors and warnings — UI displays these inline.
"""
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional


class Severity(Enum):
    ERROR   = "error"
    WARNING = "warning"
    INFO    = "info"


@dataclass
class ValidationIssue:
    severity: Severity
    field: str
    message: str


class TransformerValidator:
    """
    Validates a raw transformer config dict.
    Call validate() → list of ValidationIssue objects.
    Errors block saving; warnings are advisory.
    """

    def validate(self, data: dict) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        # Only the energizing winding's MAIN WIRES are external (permanent
        # wiring; mains applied externally, start hard-wired to the + bus), so
        # they must carry no relays. Its TAPS are routed through the matrix like
        # any other tap and each needs a B relay (RL17-32).
        ew_id = (data.get("auto_matrix") or {}).get("energize_winding")
        self._check_basic_info(data, issues)
        self._check_windings(data.get("primary",  []), "primary",  issues, ew_id)
        self._check_windings(data.get("secondary", []), "secondary", issues, ew_id)
        self._check_pin_uniqueness(data, issues)
        self._check_winding_id_uniqueness(data, issues)
        self._check_tests(data, issues)
        self._check_tap_rule_coverage(data, issues, ew_id)
        return issues

    def has_errors(self, issues: List[ValidationIssue]) -> bool:
        return any(i.severity == Severity.ERROR for i in issues)

    # ------------------------------------------------------------------ #
    #  Section checks                                                     #
    # ------------------------------------------------------------------ #

    def _check_basic_info(self, data: dict, issues: List[ValidationIssue]) -> None:
        if not str(data.get("name", "")).strip():
            issues.append(ValidationIssue(Severity.ERROR, "name",
                                           "Transformer name is required"))
        if not data.get("primary"):
            issues.append(ValidationIssue(Severity.ERROR, "primary",
                                           "At least one primary winding is required"))
        if not data.get("secondary"):
            issues.append(ValidationIssue(Severity.ERROR, "secondary",
                                           "At least one secondary winding is required"))
        if not data.get("tests"):
            issues.append(ValidationIssue(Severity.WARNING, "tests",
                                           "No test steps defined — transformer can be saved but not tested"))

    def _check_windings(self, windings: List[dict], side: str,
                        issues: List[ValidationIssue],
                        ew_id: Optional[str] = None) -> None:
        for w in windings:
            wid = str(w.get("id", "")).strip()
            is_energizing = (ew_id is not None and wid == ew_id)
            if not wid:
                issues.append(ValidationIssue(Severity.ERROR, f"{side}.id",
                                               "Winding ID cannot be empty"))
                continue

            sp = w.get("start_pin")
            ep = w.get("end_pin")
            if sp is None:
                issues.append(ValidationIssue(Severity.ERROR, f"{side}.{wid}.start_pin",
                                               f"{wid}: start_pin is required"))
            if ep is None:
                issues.append(ValidationIssue(Severity.ERROR, f"{side}.{wid}.end_pin",
                                               f"{wid}: end_pin is required"))
            if sp is not None and ep is not None and sp == ep:
                issues.append(ValidationIssue(Severity.ERROR, f"{side}.{wid}.pins",
                                               f"{wid}: start_pin and end_pin must differ"))

            # The energizing winding's MAIN WIRES are external: they carry mains,
            # are never switched, and its start is hard-wired to the + bus. So
            # they must carry no relays. (Its TAPS still take a B relay each.)
            if is_energizing:
                for fld, grp in (("relay_a", "RL1-16"), ("relay_b", "RL17-32")):
                    if w.get(fld) is not None:
                        issues.append(ValidationIssue(
                            Severity.WARNING, f"{side}.{wid}.{fld}",
                            f"{wid} is the energizing winding: its main wires are "
                            f"external and must not have {fld} ({grp}) assigned — "
                            f"only its taps take relays"))

            taps = w.get("taps", [])
            tap_pins = []
            for ti, tap in enumerate(taps):
                tpin = tap.get("pin")
                if tpin is None:
                    issues.append(ValidationIssue(Severity.ERROR,
                                                   f"{side}.{wid}.tap[{ti}].pin",
                                                   f"{wid} tap {ti}: pin number required"))
                else:
                    tap_pins.append(tpin)
                if tap.get("voltage") is None:
                    issues.append(ValidationIssue(Severity.WARNING,
                                                   f"{side}.{wid}.tap[{ti}].voltage",
                                                   f"{wid} tap {ti}: voltage not specified"))

                # Every tap takes a single relay, but from a DIFFERENT group
                # depending on the winding:
                #   • normal winding tap     → A2 group  (RL17-32)
                #   • energizing winding tap → Group B   (RL37-40)
                # (Only the energizing winding's MAIN WIRES are external; its
                #  taps are still measured.)
                rb = tap.get("relay_b")
                lo, hi, grp = ((37, 40, "Group B") if is_energizing
                               else (17, 32, "A2"))
                if rb is None:
                    issues.append(ValidationIssue(Severity.WARNING,
                                                   f"{side}.{wid}.tap[{ti}].relay_b",
                                                   f"{wid} tap {ti}: no relay assigned "
                                                   f"(needs one RL{lo}-{hi}, {grp})"))
                elif not (lo <= rb <= hi):
                    issues.append(ValidationIssue(Severity.ERROR,
                                                   f"{side}.{wid}.tap[{ti}].relay_b",
                                                   f"{wid} tap {ti}: RL{rb} is outside {grp} "
                                                   f"(RL{lo}-{hi}). "
                                                   + (f"{wid} is the energizing winding — its taps "
                                                      f"must use Group B (RL37-40), not the A2 group."
                                                      if is_energizing else
                                                      f"Only the energizing winding's taps use "
                                                      f"Group B (RL37-40).")))
                if tap.get("relay_a") is not None:
                    issues.append(ValidationIssue(Severity.WARNING,
                                                   f"{side}.{wid}.tap[{ti}].relay_a",
                                                   f"{wid} tap {ti}: relay_a is no longer used on taps — "
                                                   f"a tap takes a single relay (relay_b)"))

            # Duplicate tap pins
            if len(tap_pins) != len(set(tap_pins)):
                issues.append(ValidationIssue(Severity.WARNING, f"{side}.{wid}.taps",
                                               f"{wid}: duplicate tap pin numbers detected"))

    def _check_tap_rule_coverage(self, data: dict, issues: List[ValidationIssue],
                                 ew_id: Optional[str] = None) -> None:
        """
        Flag taps that have a relay assigned but no measurement rule referencing
        them — the stale-rules case where taps were added/changed without
        regenerating the rules, so their voltage is never measured.

        Only meaningful in rule-driven mode: when auto_matrix is enabled the
        sweep is generated dynamically (taps always included), and with no rules
        at all there is nothing to be stale against.
        """
        if (data.get("auto_matrix") or {}).get("enabled"):
            return
        rules = data.get("ratio_rules") or []
        if not rules:
            return

        # Tap nodes ("W:tap<i>") referenced by any enabled rule segment.
        measured: set = set()
        for r in rules:
            if not r.get("enabled", True):
                continue
            for seg_key in ("measurement_segment", "excitation_segment"):
                seg = r.get(seg_key) or {}
                for node in (seg.get("node_a"), seg.get("node_b")):
                    if node and ":tap" in str(node):
                        measured.add(str(node))

        # Every relayed tap must be covered — including the energizing winding's
        # taps, which ARE routed through the matrix (only its main wires aren't).
        for side in ("primary", "secondary"):
            for w in data.get(side) or []:
                wid = str(w.get("id", ""))
                for ti, tap in enumerate(w.get("taps") or []):
                    if tap.get("relay_b") is None:
                        continue   # unrelayed tap — covered by the relay-assignment check
                    if f"{wid}:tap{ti}" not in measured:
                        issues.append(ValidationIssue(Severity.WARNING,
                                                       f"{side}.{wid}.tap[{ti}]",
                                                       f"{wid} tap {ti}: has relay RL{tap['relay_b']} but no "
                                                       f"measurement rule — regenerate rules so it is tested"))

    def _check_pin_uniqueness(self, data: dict, issues: List[ValidationIssue]) -> None:
        # A pin is normally an int (== its relay number), but the energizing
        # winding's two mains wires carry no relay and are labelled "EN+"/"EN-".
        # Compare as strings so both kinds coexist.
        all_pins: List[str] = []
        for w in data.get("primary", []) + data.get("secondary", []):
            for p in (w.get("start_pin"), w.get("end_pin")):
                if p is not None:
                    all_pins.append(str(p))
            for tap in w.get("taps", []):
                if tap.get("pin") is not None:
                    all_pins.append(str(tap["pin"]))

        seen: set = set()
        for pin in all_pins:
            if pin in seen:
                issues.append(ValidationIssue(Severity.WARNING, "pins",
                                               f"Pin {pin} appears on more than one winding/tap"))
            seen.add(pin)

    def _check_winding_id_uniqueness(self, data: dict,
                                      issues: List[ValidationIssue]) -> None:
        ids: List[str] = []
        for w in data.get("primary", []) + data.get("secondary", []):
            wid = str(w.get("id", "")).strip()
            if wid:
                ids.append(wid)
        if len(ids) != len(set(ids)):
            issues.append(ValidationIssue(Severity.ERROR, "winding_ids",
                                           "Duplicate winding IDs found — each winding must have a unique ID"))

    def _check_tests(self, data: dict, issues: List[ValidationIssue]) -> None:
        all_ids = {
            str(w.get("id", "")).strip()
            for w in data.get("primary", []) + data.get("secondary", [])
            if w.get("id")
        }
        for i, test in enumerate(data.get("tests", [])):
            from_w = str(test.get("from", "")).strip()
            to_w   = str(test.get("to",   "")).strip()
            if from_w and from_w not in all_ids:
                issues.append(ValidationIssue(Severity.ERROR,
                                               f"tests[{i}].from",
                                               f"Test {i+1}: winding '{from_w}' not defined"))
            if to_w and to_w not in all_ids:
                issues.append(ValidationIssue(Severity.ERROR,
                                               f"tests[{i}].to",
                                               f"Test {i+1}: winding '{to_w}' not defined"))
            if test.get("expected_voltage") is None:
                issues.append(ValidationIssue(Severity.ERROR,
                                               f"tests[{i}].expected_voltage",
                                               f"Test {i+1}: expected_voltage is required"))
