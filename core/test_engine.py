"""
Test engine — orchestrates the automated test sequence.
Runs in a background thread; posts all UI updates via StateManager notifications.
Supports AUTO / MANUAL modes, pause, stop, retry, and emergency stop.
"""
import time
import threading
import queue
from enum import Enum, auto
from typing import Optional, Callable

from core.state_manager import StateManager, AppState, TestMode, TestStepResult
from core.sequence_manager import SequenceManager, ExecutableStep
from core.config_loader import ConfigLoader, TransformerConfig
from core.logger import TestLogger
from hardware.hardware_interface import HardwareManagerInterface, HardwareStatus


class EngineCommand(Enum):
    START        = auto()
    STOP         = auto()
    PAUSE        = auto()
    RESUME       = auto()
    NEXT_STEP    = auto()   # manual mode: advance one step
    RETRY_STEP   = auto()
    EMERGENCY    = auto()


class TestEngine:
    """
    Drives the test sequence.
    State machine: IDLE → TESTING → (PASS|FAIL) → IDLE
    All hardware interactions go through HardwareManagerInterface.
    All state changes go through StateManager (which notifies UI).
    """

    SETTLE_POLL_MS = 50   # poll interval while waiting for stabilization

    def __init__(self,
                 state_manager: StateManager,
                 sequence_manager: SequenceManager,
                 config_loader: ConfigLoader,
                 hardware: HardwareManagerInterface,
                 logger: TestLogger):
        self._state = state_manager
        self._seq = sequence_manager
        self._configs = config_loader
        self._hw = hardware
        self._log = logger

        self._cmd_queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._paused = threading.Event()
        self._paused.set()   # not paused initially
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------ #
    #  Public command API (called from UI thread)                         #
    # ------------------------------------------------------------------ #

    def start(self, operator: str = "") -> None:
        if self._state.app_state in (AppState.TESTING, AppState.PAUSED):
            return
        tid = self._state.selected_transformer_id
        if not tid:
            self._log.error("No transformer selected")
            return
        config = self._configs.get_transformer(tid)
        if not config:
            self._log.error(f"Transformer config not found: {tid}")
            return
        if not operator:
            operator = self._state.operator_name or "Unknown"

        self._stop_event.clear()
        self._paused.set()
        self._cmd_queue.put(("START", {"config": config, "operator": operator}))
        self._ensure_thread()

    def stop(self) -> None:
        self._stop_event.set()
        self._paused.set()   # unblock if paused
        self._cmd_queue.put(("STOP", {}))

    def pause(self) -> None:
        if self._state.app_state == AppState.TESTING:
            self._paused.clear()
            self._state.app_state = AppState.PAUSED
            self._log.info("Test paused")

    def resume(self) -> None:
        if self._state.app_state == AppState.PAUSED:
            self._paused.set()
            self._state.app_state = AppState.TESTING
            self._log.info("Test resumed")

    def next_step(self) -> None:
        """Manual mode: execute next single step."""
        self._cmd_queue.put(("NEXT_STEP", {}))

    def retry_step(self) -> None:
        self._cmd_queue.put(("RETRY_STEP", {}))

    def emergency_stop(self) -> None:
        self._stop_event.set()
        self._paused.set()
        self._hw.emergency_stop()
        self._state.app_state = AppState.EMERGENCY
        self._log.error("EMERGENCY STOP activated — all relays opened")

    # ------------------------------------------------------------------ #
    #  Thread management                                                  #
    # ------------------------------------------------------------------ #

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="TestEngine")
            self._thread.start()

    def _run(self) -> None:
        """Main engine loop — processes commands from the queue."""
        while True:
            try:
                cmd, payload = self._cmd_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if cmd == "START":
                self._execute_sequence(payload["config"], payload["operator"])
            elif cmd == "STOP":
                self._do_stop()
                break
            elif cmd == "NEXT_STEP":
                self._do_manual_step()
            elif cmd == "RETRY_STEP":
                self._do_retry()

    # ------------------------------------------------------------------ #
    #  Sequence execution                                                 #
    # ------------------------------------------------------------------ #

    def _execute_sequence(self, config: TransformerConfig, operator: str) -> None:
        steps = self._seq.load(config)
        total = len(steps)

        self._state.begin_session(config.transformer_id, operator, total)
        self._state.app_state = AppState.TESTING
        self._log.info(f"Starting test: {config.name} ({total} steps) — Operator: {operator}")

        hw_health = self._hw.health_check()
        for subsystem, status in hw_health.items():
            if status != HardwareStatus.CONNECTED:
                self._log.warn(f"Hardware subsystem '{subsystem}' not connected — running in simulation")

        all_pass = True

        for step in steps:
            if self._stop_event.is_set():
                self._log.info("Test stopped by operator")
                break

            self._paused.wait()   # block here if paused

            if self._stop_event.is_set():
                break

            result = self._execute_step(step, total)
            if not result.passed:
                all_pass = False

            if self._state.test_mode == TestMode.AUTO:
                # small inter-step gap for UI animation to complete
                time.sleep(0.4)
            else:
                # manual mode: wait for NEXT_STEP command
                self._log.info("Manual mode — waiting for Next Step command")
                cmd, _ = self._cmd_queue.get()   # block
                if cmd == "STOP":
                    break

        if not self._stop_event.is_set():
            final_state = AppState.PASS if all_pass else AppState.FAIL
            self._state.app_state = final_state
            session = self._state.end_session(all_pass)
            if session:
                paths = self._log.save_session(session, operator)
                self._log.info(f"Test complete — {'PASS' if all_pass else 'FAIL'}")
        else:
            self._do_stop()

    def _execute_step(self, step: ExecutableStep, total: int) -> TestStepResult:
        self._log.step(f"Step {step.index+1}/{total}: {step.description}")

        # Update UI state
        self._state.set_test_step(
            step.index, total,
            step.from_winding_id, step.to_winding_id,
            step.expected_voltage, step.tolerance_percent,
            from_tap_index=step.from_tap_index,
            to_tap_index=step.to_tap_index,
        )

        # Open all relays first (safe break-before-make)
        self._hw.relays.reset_all_relays()
        time.sleep(0.05)

        # Set relays atomically — safety enforcement (group exclusivity + gate
        # relays) is handled inside the relay controller implementation.
        ok = self._hw.relays.set_all_relays(step.relay_map)
        if not ok:
            self._log.warn(f"Relay set not fully acknowledged for step {step.index + 1}")
        self._state.set_relay_states(step.relay_map)

        # Inject simulated voltage for mock hardware
        try:
            self._hw.simulate_test_voltages(  # only exists on MockHardwareManager
                step.expected_voltage,
                channel=step.measurement_channel,
                deviation_pct=1.5,
            )
        except AttributeError:
            pass   # real hardware — no simulation needed

        # Wait stabilization
        wait_s = step.stabilization_delay_ms / 1000.0
        time.sleep(wait_s)

        # Read voltage
        reading = self._hw.voltmeter.read_voltage(step.measurement_channel)

        if not reading.valid:
            result = TestStepResult(
                step_index=step.index,
                from_winding=step.from_winding_id,
                to_winding=step.to_winding_id,
                measured_voltage=0.0,
                expected_voltage=step.expected_voltage,
                tolerance_percent=step.tolerance_percent,
                passed=False,
                timestamp=time.time(),
                error=reading.error or "Voltage read error",
            )
            self._log.error(f"  Measurement error: {reading.error}")
        else:
            measured = reading.voltage
            self._state.set_measurement(measured)
            tol_v = step.expected_voltage * (step.tolerance_percent / 100.0)
            passed = abs(measured - step.expected_voltage) <= tol_v
            result = TestStepResult(
                step_index=step.index,
                from_winding=step.from_winding_id,
                to_winding=step.to_winding_id,
                measured_voltage=measured,
                expected_voltage=step.expected_voltage,
                tolerance_percent=step.tolerance_percent,
                passed=passed,
                timestamp=time.time(),
            )
            status_str = "PASS" if passed else "FAIL"
            tap_info = ""
            if step.from_tap_index is not None:
                tap_info += f"[from_tap:{step.from_tap_index}] "
            if step.to_tap_index is not None:
                tap_info += f"[to_tap:{step.to_tap_index}] "
            self._log.step(
                f"  {step.from_winding_id}→{step.to_winding_id} {tap_info}"
                f"Measured={measured:.3f}V  Expected={step.expected_voltage}V  "
                f"Tol=±{step.tolerance_percent}%  [{status_str}]"
            )

        self._state.set_step_result(result)

        # Open relays after measurement
        self._hw.relays.reset_all_relays()
        self._state.set_relay_states({})

        return result

    def _do_stop(self) -> None:
        self._hw.relays.reset_all_relays()
        self._state.set_relay_states({})
        if self._state.app_state not in (AppState.EMERGENCY,):
            self._state.app_state = AppState.IDLE
        self._log.info("Test engine stopped — all relays opened")

    def _do_manual_step(self) -> None:
        step = self._seq.current_step()
        if step:
            total = self._seq.total_steps
            self._execute_step(step, total)

    def _do_retry(self) -> None:
        step = self._seq.current_step()
        if step:
            self._log.info(f"Retrying step {step.index+1}: {step.description}")
            total = self._seq.total_steps
            self._execute_step(step, total)
