"""
Application state manager.
Central observable state store — all modules read from here, post changes here.
Observers register callbacks and are notified on state changes (thread-safe).
"""
import threading
import time
import uuid
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field


class AppState(Enum):
    IDLE         = "IDLE"
    INITIALIZING = "INITIALIZING"
    READY        = "READY"
    TESTING      = "TESTING"
    PAUSED       = "PAUSED"
    STOPPING     = "STOPPING"
    PASS         = "PASS"
    FAIL         = "FAIL"
    ERROR        = "ERROR"
    EMERGENCY    = "EMERGENCY"


class TestMode(Enum):
    AUTO   = "AUTO"
    MANUAL = "MANUAL"


@dataclass
class TestStepResult:
    step_index: int
    from_winding: str
    to_winding: str
    measured_voltage: float
    expected_voltage: float
    tolerance_percent: float
    passed: bool
    timestamp: float
    error: Optional[str] = None

    @property
    def deviation_pct(self) -> float:
        if self.expected_voltage == 0:
            return 0.0
        return abs(self.measured_voltage - self.expected_voltage) / self.expected_voltage * 100.0


@dataclass
class TestSession:
    transformer_id: str
    operator: str
    start_time: float
    results: List[TestStepResult] = field(default_factory=list)
    end_time: Optional[float] = None
    overall_pass: Optional[bool] = None

    @property
    def total_steps(self) -> int:
        return len(self.results)

    @property
    def passed_steps(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def duration(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time


@dataclass
class UnitResult:
    unit_number: int
    transformer_id: str
    passed: Optional[bool]
    skipped: bool
    skip_reason: str
    start_time: float
    end_time: Optional[float] = None
    passed_steps: int = 0
    total_steps: int = 0

    @property
    def duration(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time


@dataclass
class BatchSession:
    batch_id: str
    operator: str
    transformer_id: str
    start_time: float
    units: List[UnitResult] = field(default_factory=list)
    end_time: Optional[float] = None

    @property
    def active_unit_number(self) -> int:
        return len(self.units) + 1

    @property
    def total_tested(self) -> int:
        return len([u for u in self.units if not u.skipped])

    @property
    def pass_count(self) -> int:
        return len([u for u in self.units if u.passed is True])

    @property
    def fail_count(self) -> int:
        return len([u for u in self.units if u.passed is False])

    @property
    def skip_count(self) -> int:
        return len([u for u in self.units if u.skipped])

    def to_summary(self, active: bool = True) -> dict:
        return {
            "active":         active,
            "batch_id":       self.batch_id,
            "operator":       self.operator,
            "transformer_id": self.transformer_id,
            "start_time":     self.start_time,
            "unit_count":     len(self.units),
            "pass_count":     self.pass_count,
            "fail_count":     self.fail_count,
            "skip_count":     self.skip_count,
        }


class StateManager:
    """
    Thread-safe observable state container.
    Modules call setters to change state; UI callbacks receive change notifications.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._observers: Dict[str, List[Callable]] = {}

        self._app_state: AppState = AppState.IDLE
        self._test_mode: TestMode = TestMode.AUTO
        self._selected_transformer_id: Optional[str] = None
        self._operator_name: str = ""
        self._current_step_index: int = -1
        self._total_steps: int = 0
        self._active_primary: Optional[str] = None
        self._active_secondary: Optional[str] = None
        self._current_measurement: Optional[float] = None
        self._current_expected: Optional[float] = None
        self._current_tolerance: float = 5.0
        self._last_step_result: Optional[TestStepResult] = None
        self._current_session: Optional[TestSession] = None
        self._batch_session: Optional[BatchSession] = None
        self._relay_states: Dict[int, bool] = {}
        self._error_message: str = ""
        # ── excitation / ratio state ──────────────────────────────────────
        self._excitation_winding_id: Optional[str] = None
        self._applied_voltage: Optional[float] = None
        self._nominal_excitation_voltage: Optional[float] = None
        self._ratio_factor: Optional[float] = None

    # ── Observer pattern ─────────────────────────────────────────────────────

    def subscribe(self, key: str, callback: Callable[[Any], None]) -> None:
        with self._lock:
            self._observers.setdefault(key, []).append(callback)

    def unsubscribe(self, key: str, callback: Callable) -> None:
        with self._lock:
            if key in self._observers:
                self._observers[key] = [c for c in self._observers[key] if c != callback]

    def _notify(self, key: str, value: Any) -> None:
        callbacks = []
        with self._lock:
            callbacks = list(self._observers.get(key, []))
        for cb in callbacks:
            try:
                cb(value)
            except Exception as e:
                print(f"[StateManager] Observer error on '{key}': {e}")

    # ── Fire helpers (for events without corresponding state property) ───────

    def fire_relays_cleared(self) -> None:
        self._notify("relays_cleared", None)

    def fire_test_paused(self) -> None:
        self._notify("test_paused", None)

    def fire_test_resumed(self) -> None:
        self._notify("test_resumed", None)

    def fire_test_stopped(self) -> None:
        self._notify("test_stopped", None)

    def fire_estop(self) -> None:
        self._notify("estop_triggered", None)

    def fire_excitation_config(self) -> None:
        self._notify("excitation_config", {
            "excitation_winding_id":      self._excitation_winding_id,
            "applied_voltage":            self._applied_voltage,
            "nominal_excitation_voltage": self._nominal_excitation_voltage,
            "ratio_factor":               self._ratio_factor,
        })

    # ── State properties ──────────────────────────────────────────────────────

    @property
    def app_state(self) -> AppState:
        with self._lock:
            return self._app_state

    @app_state.setter
    def app_state(self, value: AppState) -> None:
        with self._lock:
            self._app_state = value
        self._notify("app_state", value)

    @property
    def test_mode(self) -> TestMode:
        with self._lock:
            return self._test_mode

    @test_mode.setter
    def test_mode(self, value: TestMode) -> None:
        with self._lock:
            self._test_mode = value
        self._notify("test_mode", value)

    @property
    def selected_transformer_id(self) -> Optional[str]:
        with self._lock:
            return self._selected_transformer_id

    @selected_transformer_id.setter
    def selected_transformer_id(self, value: Optional[str]) -> None:
        with self._lock:
            self._selected_transformer_id = value
        self._notify("selected_transformer_id", value)

    @property
    def operator_name(self) -> str:
        with self._lock:
            return self._operator_name

    @operator_name.setter
    def operator_name(self, value: str) -> None:
        with self._lock:
            self._operator_name = value
        self._notify("operator_name", value)

    @property
    def current_step_index(self) -> int:
        with self._lock:
            return self._current_step_index

    @property
    def total_steps(self) -> int:
        with self._lock:
            return self._total_steps

    @property
    def progress_pct(self) -> float:
        with self._lock:
            if self._total_steps == 0:
                return 0.0
            return (self._current_step_index + 1) / self._total_steps * 100.0

    @property
    def active_primary(self) -> Optional[str]:
        with self._lock:
            return self._active_primary

    @property
    def active_secondary(self) -> Optional[str]:
        with self._lock:
            return self._active_secondary

    @property
    def current_measurement(self) -> Optional[float]:
        with self._lock:
            return self._current_measurement

    @property
    def current_expected(self) -> Optional[float]:
        with self._lock:
            return self._current_expected

    @property
    def current_tolerance(self) -> float:
        with self._lock:
            return self._current_tolerance

    @property
    def last_step_result(self) -> Optional[TestStepResult]:
        with self._lock:
            return self._last_step_result

    @property
    def current_session(self) -> Optional[TestSession]:
        with self._lock:
            return self._current_session

    @property
    def batch_session(self) -> Optional[BatchSession]:
        with self._lock:
            return self._batch_session

    @property
    def relay_states(self) -> Dict[int, bool]:
        with self._lock:
            return dict(self._relay_states)

    @property
    def error_message(self) -> str:
        with self._lock:
            return self._error_message

    # ── Excitation / ratio properties ────────────────────────────────────────

    @property
    def excitation_winding_id(self) -> Optional[str]:
        with self._lock:
            return self._excitation_winding_id

    @property
    def applied_voltage(self) -> Optional[float]:
        with self._lock:
            return self._applied_voltage

    @property
    def nominal_excitation_voltage(self) -> Optional[float]:
        with self._lock:
            return self._nominal_excitation_voltage

    @property
    def ratio_factor(self) -> Optional[float]:
        with self._lock:
            return self._ratio_factor

    def set_excitation(self, winding_id: Optional[str],
                       applied_voltage: Optional[float],
                       nominal_voltage: Optional[float]) -> None:
        with self._lock:
            self._excitation_winding_id      = winding_id
            self._applied_voltage            = applied_voltage
            self._nominal_excitation_voltage = nominal_voltage
            if applied_voltage and nominal_voltage:
                self._ratio_factor = applied_voltage / nominal_voltage
            else:
                self._ratio_factor = None
        self.fire_excitation_config()

    # ── Compound update methods ───────────────────────────────────────────────

    def set_test_step(self, step_index: int, total: int,
                      from_w: str, to_w: str,
                      expected: float, tolerance: float,
                      from_tap_index: Optional[int] = None,
                      to_tap_index: Optional[int] = None,
                      nominal_output_voltage: Optional[float] = None,
                      is_ratio_step: bool = False) -> None:
        with self._lock:
            self._current_step_index = step_index
            self._total_steps = total
            self._active_primary = from_w
            self._active_secondary = to_w
            self._current_expected = expected
            self._current_tolerance = tolerance
            self._current_measurement = None
            ratio = self._ratio_factor
        self._notify("test_step", {
            "step_index": step_index, "total": total,
            "from_w": from_w, "to_w": to_w,
            "expected": expected, "tolerance": tolerance,
            "from_tap_index": from_tap_index,
            "to_tap_index": to_tap_index,
            "nominal_output_voltage": nominal_output_voltage,
            "ratio_factor": ratio,
            "is_ratio_step": is_ratio_step,
        })

    def set_measurement(self, voltage: float) -> None:
        with self._lock:
            self._current_measurement = voltage
        self._notify("measurement", voltage)

    def set_step_result(self, result: TestStepResult) -> None:
        with self._lock:
            self._last_step_result = result
            if self._current_session:
                self._current_session.results.append(result)
        self._notify("step_result", result)

    def set_relay_states(self, states: Dict[int, bool]) -> None:
        with self._lock:
            self._relay_states.update(states)
        self._notify("relay_states", dict(self._relay_states))

    def reset_relay_states(self) -> None:
        with self._lock:
            self._relay_states.clear()
        self._notify("relay_states", {})

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def begin_session(self, transformer_id: str, operator: str, total_steps: int) -> None:
        with self._lock:
            self._current_session = TestSession(
                transformer_id=transformer_id,
                operator=operator,
                start_time=time.time(),
            )
            self._total_steps = total_steps
            self._current_step_index = -1
        self._notify("session_started", self._current_session)

    def end_session(self, overall_pass: bool) -> Optional[TestSession]:
        with self._lock:
            session = self._current_session
            if session:
                session.end_time = time.time()
                session.overall_pass = overall_pass
        self._notify("session_ended", session)
        return session

    # ── Batch lifecycle ───────────────────────────────────────────────────────

    def begin_batch(self, transformer_id: str, operator: str) -> str:
        batch_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._batch_session = BatchSession(
                batch_id=batch_id,
                operator=operator,
                transformer_id=transformer_id,
                start_time=time.time(),
            )
        self._notify("batch_state", self._batch_session.to_summary(active=True))
        return batch_id

    def add_unit_result(self, unit_result: UnitResult) -> None:
        with self._lock:
            if self._batch_session is not None:
                self._batch_session.units.append(unit_result)
                summary = self._batch_session.to_summary(active=True)
            else:
                summary = None
        if summary:
            self._notify("batch_state", summary)
        if unit_result.skipped:
            self._notify("unit_skipped", unit_result)
        else:
            self._notify("unit_completed", unit_result)

    def end_batch(self) -> Optional[BatchSession]:
        with self._lock:
            batch = self._batch_session
            if batch:
                batch.end_time = time.time()
            self._batch_session = None
        if batch:
            self._notify("batch_state", batch.to_summary(active=False))
        return batch

    # ── Error / reset ─────────────────────────────────────────────────────────

    def set_error(self, message: str) -> None:
        with self._lock:
            self._error_message = message
            self._app_state = AppState.ERROR
        self._notify("error", message)
        self._notify("app_state", AppState.ERROR)

    def reset(self) -> None:
        with self._lock:
            self._app_state = AppState.READY
            self._current_step_index = -1
            self._total_steps = 0
            self._active_primary = None
            self._active_secondary = None
            self._current_measurement = None
            self._current_expected = None
            self._last_step_result = None
            self._error_message = ""
            self._excitation_winding_id = None
            self._applied_voltage = None
            self._nominal_excitation_voltage = None
            self._ratio_factor = None
        self._notify("app_state", AppState.READY)
        self._notify("reset", None)
