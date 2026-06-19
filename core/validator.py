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
        self._check_basic_info(data, issues)
        self._check_windings(data.get("primary",  []), "primary",  issues)
        self._check_windings(data.get("secondary", []), "secondary", issues)
        self._check_pin_uniqueness(data, issues)
        self._check_winding_id_uniqueness(data, issues)
        self._check_tests(data, issues)
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
                        issues: List[ValidationIssue]) -> None:
        for w in windings:
            wid = str(w.get("id", "")).strip()
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

                # A tap takes a single B-side relay (RL17-32); measured start→tap.
                if tap.get("relay_b") is None:
                    issues.append(ValidationIssue(Severity.WARNING,
                                                   f"{side}.{wid}.tap[{ti}].relay_b",
                                                   f"{wid} tap {ti}: no relay assigned (needs one RL17-32)"))
                if tap.get("relay_a") is not None:
                    issues.append(ValidationIssue(Severity.WARNING,
                                                   f"{side}.{wid}.tap[{ti}].relay_a",
                                                   f"{wid} tap {ti}: relay_a is no longer used on taps — "
                                                   f"a tap takes a single relay (relay_b)"))

            # Duplicate tap pins
            if len(tap_pins) != len(set(tap_pins)):
                issues.append(ValidationIssue(Severity.WARNING, f"{side}.{wid}.taps",
                                               f"{wid}: duplicate tap pin numbers detected"))

    def _check_pin_uniqueness(self, data: dict, issues: List[ValidationIssue]) -> None:
        all_pins: List[int] = []
        for w in data.get("primary", []) + data.get("secondary", []):
            for p in (w.get("start_pin"), w.get("end_pin")):
                if p is not None:
                    all_pins.append(int(p))
            for tap in w.get("taps", []):
                if tap.get("pin") is not None:
                    all_pins.append(int(tap["pin"]))

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
