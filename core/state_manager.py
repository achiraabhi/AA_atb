"""
Application state manager.
Central observable state store — all modules read from here, post changes here.
Observers register callbacks and are notified on state changes (thread-safe).
"""
import threading
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field


class AppState(Enum):
    IDLE         = auto()
    INITIALIZING = auto()
    READY        = auto()
    TESTING      = auto()
    PAUSED       = auto()
    PASS         = auto()
    FAIL         = auto()
    ERROR        = auto()
    EMERGENCY    = auto()


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


class StateManager:
    """
    Thread-safe observable state container.
    Modules call set() to change state; UI callbacks receive change notifications.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._observers: Dict[str, List[Callable]] = {}

        # Core state fields
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
        self._relay_states: Dict[int, bool] = {}
        self._error_message: str = ""

    # ------------------------------------------------------------------ #
    #  Observer pattern                                                   #
    # ------------------------------------------------------------------ #

    def subscribe(self, key: str, callback: Callable[[Any], None]) -> None:
        """Register callback to be called when `key` changes."""
        with self._lock:
            self._observers.setdefault(key, []).append(callback)

    def unsubscribe(self, key: str, callback: Callable) -> None:
        with self._lock:
            if key in self._observers:
                self._observers[key] = [c for c in self._observers[key] if c != callback]

    def _notify(self, key: str, value: Any) -> None:
        # Called while lock is NOT held to avoid deadlock
        callbacks = []
        with self._lock:
            callbacks = list(self._observers.get(key, []))
        for cb in callbacks:
            try:
                cb(value)
            except Exception as e:
                print(f"[StateManager] Observer error on '{key}': {e}")

    # ------------------------------------------------------------------ #
    #  State properties                                                    #
    # ------------------------------------------------------------------ #

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
    def relay_states(self) -> Dict[int, bool]:
        with self._lock:
            return dict(self._relay_states)

    @property
    def error_message(self) -> str:
        with self._lock:
            return self._error_message

    # ------------------------------------------------------------------ #
    #  Compound update methods (called by test engine)                    #
    # ------------------------------------------------------------------ #

    def set_test_step(self, step_index: int, total: int,
                      from_w: str, to_w: str,
                      expected: float, tolerance: float,
                      from_tap_index: Optional[int] = None,
                      to_tap_index: Optional[int] = None) -> None:
        with self._lock:
            self._current_step_index = step_index
            self._total_steps = total
            self._active_primary = from_w
            self._active_secondary = to_w
            self._current_expected = expected
            self._current_tolerance = tolerance
            self._current_measurement = None
        self._notify("test_step", {
            "step_index": step_index, "total": total,
            "from_w": from_w, "to_w": to_w,
            "expected": expected, "tolerance": tolerance,
            "from_tap_index": from_tap_index,
            "to_tap_index": to_tap_index,
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

    def begin_session(self, transformer_id: str, operator: str, total_steps: int) -> None:
        import time
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
        import time
        with self._lock:
            session = self._current_session
            if session:
                session.end_time = time.time()
                session.overall_pass = overall_pass
        self._notify("session_ended", session)
        return session

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
        self._notify("app_state", AppState.READY)
        self._notify("reset", None)
