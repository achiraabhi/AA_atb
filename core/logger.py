"""
Test result logger — writes CSV and JSON reports to the logs/ directory.
Thread-safe; safe to call from background test threads.
"""
import csv
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from core.state_manager import TestSession, TestStepResult


class TestLogger:
    """Writes test results to CSV and JSON log files."""

    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            base = Path(__file__).parent.parent
            log_dir = str(base / "logs")
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._console_callbacks = []

    # ------------------------------------------------------------------ #
    #  Console log (in-app display)                                       #
    # ------------------------------------------------------------------ #

    def add_console_callback(self, cb) -> None:
        self._console_callbacks.append(cb)

    def log(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {message}"
        for cb in self._console_callbacks:
            try:
                cb(line)
            except Exception:
                pass

    def info(self, msg: str) -> None:
        self.log("INFO", msg)

    def warn(self, msg: str) -> None:
        self.log("WARN", msg)

    def error(self, msg: str) -> None:
        self.log("ERROR", msg)

    def step(self, msg: str) -> None:
        self.log("STEP", msg)

    # ------------------------------------------------------------------ #
    #  Persistent logging                                                 #
    # ------------------------------------------------------------------ #

    def save_session(self, session: TestSession, operator: str = "") -> Dict[str, str]:
        """
        Write session results to CSV and JSON.
        Returns dict with file paths.
        """
        stamp = datetime.fromtimestamp(session.start_time).strftime("%Y%m%d_%H%M%S")
        base_name = f"{session.transformer_id}_{stamp}"

        csv_path = self._log_dir / f"{base_name}.csv"
        json_path = self._log_dir / f"{base_name}.json"

        with self._lock:
            self._write_csv(csv_path, session, operator)
            self._write_json(json_path, session, operator)

        self.info(f"Results saved: {csv_path.name}, {json_path.name}")
        return {"csv": str(csv_path), "json": str(json_path)}

    def _write_csv(self, path: Path, session: TestSession, operator: str) -> None:
        fieldnames = [
            "timestamp", "transformer_id", "operator",
            "step", "from_winding", "to_winding",
            "measured_v", "expected_v", "tolerance_pct",
            "deviation_pct", "result", "error",
        ]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in session.results:
                writer.writerow({
                    "timestamp":      datetime.fromtimestamp(r.timestamp).isoformat(),
                    "transformer_id": session.transformer_id,
                    "operator":       operator or session.operator,
                    "step":           r.step_index + 1,
                    "from_winding":   r.from_winding,
                    "to_winding":     r.to_winding,
                    "measured_v":     f"{r.measured_voltage:.3f}",
                    "expected_v":     f"{r.expected_voltage:.3f}",
                    "tolerance_pct":  f"{r.tolerance_percent:.1f}",
                    "deviation_pct":  f"{r.deviation_pct:.2f}",
                    "result":         "PASS" if r.passed else "FAIL",
                    "error":          r.error or "",
                })

    def _write_json(self, path: Path, session: TestSession, operator: str) -> None:
        data = {
            "transformer_id": session.transformer_id,
            "operator":       operator or session.operator,
            "start_time":     datetime.fromtimestamp(session.start_time).isoformat(),
            "end_time":       datetime.fromtimestamp(session.end_time).isoformat()
                              if session.end_time else None,
            "overall_result": "PASS" if session.overall_pass else "FAIL",
            "total_steps":    session.total_steps,
            "passed_steps":   session.passed_steps,
            "steps": [
                {
                    "step":          r.step_index + 1,
                    "from":          r.from_winding,
                    "to":            r.to_winding,
                    "measured_v":    round(r.measured_voltage, 3),
                    "expected_v":    round(r.expected_voltage, 3),
                    "tolerance_pct": r.tolerance_percent,
                    "deviation_pct": round(r.deviation_pct, 2),
                    "result":        "PASS" if r.passed else "FAIL",
                    "timestamp":     datetime.fromtimestamp(r.timestamp).isoformat(),
                    "error":         r.error,
                }
                for r in session.results
            ],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def list_log_files(self) -> List[Path]:
        return sorted(self._log_dir.glob("*.json"), reverse=True)
