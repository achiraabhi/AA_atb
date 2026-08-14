"""
Test engine — orchestrates the automated test sequence.
Runs in a background thread; posts all UI updates via StateManager notifications.

State machine:
  IDLE → START → TESTING → PASS/FAIL → (NEXT_UNIT | RETRY_UNIT | SKIP_UNIT | COMPLETE_BATCH)
  TESTING → PAUSE → PAUSED → RESUME → TESTING
  TESTING | PAUSED → STOP → IDLE
  ANY → EMERGENCY → EMERGENCY

The engine thread stays alive between units waiting for the next batch command.
Stop/complete-batch are the only ways to exit the engine thread.
"""
import os
import time
import threading
import queue
from typing import Optional

from core.state_manager import (
    StateManager, AppState, TestMode, TestStepResult,
    UnitResult, BatchSession,
)
from core.sequence_manager import SequenceManager, ExecutableStep
from core.config_loader import ConfigLoader, TransformerConfig
from core.ratio_engine import TransformerRatioEngine
from core.logger import TestLogger
from hardware.hardware_interface import HardwareManagerInterface, HardwareStatus


# Hold the winding's relays closed for this long on every measurement so the
# reading is taken on a fully-settled circuit. Applied as a floor: a step whose
# stabilization_delay_ms asks for longer still gets the longer settle.
MEASUREMENT_RELAY_HOLD_S = 2.0


class TestEngine:

    def __init__(self,
                 state_manager: StateManager,
                 sequence_manager: SequenceManager,
                 config_loader: ConfigLoader,
                 hardware: HardwareManagerInterface,
                 logger: TestLogger,
                 store=None):
        self._state = state_manager
        self._seq   = sequence_manager
        self._cfgs  = config_loader
        self._hw    = hardware
        self._log   = logger
        self._store = store   # db.store.ResultStore | None (persistence is optional)

        self._cmd_queue: queue.Queue        = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._paused           = threading.Event()
        self._paused.set()                   # not paused initially
        self._stop_event       = threading.Event()
        self._manual_step_event = threading.Event()

        self._current_config:             Optional[TransformerConfig] = None
        self._current_operator:           str = ""
        self._excitation_winding_id:      Optional[str]   = None
        self._applied_voltage:            Optional[float] = None
        self._nominal_excitation_voltage: Optional[float] = None
        self._ratio_factor:               Optional[float] = None
        self._ratio_engine = TransformerRatioEngine()

    # ── winding-polarity (phase) check ──────────────────────────────────────────

    def _read_step_phase(self, step) -> Optional[bool]:
        """Read the phase-detect pin for this step, or None when it doesn't apply.

        Applies ONLY to a main winding's full-winding measurement (energizing↔
        main). Excluded: taps (to_tap_index set) and the energizing winding
        itself. Disabled entirely by PHASE_CHECK=off. True=in-phase, False=out-
        of-phase, None=not applicable / board unavailable."""
        if os.getenv("PHASE_CHECK", "on").strip().lower() in ("off", "0", "false", "no"):
            return None
        if step.to_tap_index is not None:
            return None
        if self._excitation_winding_id and step.to_winding_id == self._excitation_winding_id:
            return None
        try:
            return self._hw.relays.read_phase()
        except Exception as e:
            self._log.warn(f"Phase read failed: {e}")
            return None

    # ── persistence ────────────────────────────────────────────────────────────

    def _persist_unit(self, config, session, unit_result) -> None:
        """Dual-write a completed/skipped unit to the database (best-effort).

        Runs alongside the existing CSV/JSON export — the file logs are unchanged;
        this just also lands the data in SQLite so it is queryable and survives
        restarts. Never raises: a DB failure must not disturb the test flow."""
        if self._store is None:
            return
        try:
            config_raw = None
            tid = getattr(config, "transformer_id", None) or getattr(unit_result, "transformer_id", None)
            if tid:
                try:
                    config_raw = self._cfgs.get_raw_json(tid)
                except Exception:
                    config_raw = None
            self._store.save_unit_result(
                config_raw=config_raw,
                session=session,
                unit_result=unit_result,
                batch=self._state.batch_session,
                operator=self._current_operator or self._state.operator_name,
                applied_voltage=self._applied_voltage,
                ratio_factor=self._ratio_factor,
                nominal_excitation_voltage=self._nominal_excitation_voltage,
            )
        except Exception as e:
            self._log.warn(f"DB persist failed: {e}")

    # ── Public command API ────────────────────────────────────────────────────

    def start(self, operator: str = "",
              excitation_winding_id: Optional[str] = None,
              applied_voltage: Optional[float] = None) -> None:
        s = self._state.app_state
        if s in (AppState.TESTING, AppState.PAUSED, AppState.STOPPING):
            return
        tid = self._state.selected_transformer_id
        if not tid:
            self._log.error("No transformer selected")
            return
        config = self._cfgs.get_transformer(tid)
        if not config:
            self._log.error(f"Transformer config not found: {tid}")
            return
        if not operator:
            operator = self._state.operator_name or "Unknown"

        # Resolve excitation winding nominal voltage
        nominal_excitation = None
        if excitation_winding_id and applied_voltage is not None:
            w = config.get_winding(excitation_winding_id)
            if w:
                nominal_excitation = w.nominal_voltage
            else:
                self._log.warn(f"Excitation winding '{excitation_winding_id}' not found — ratio disabled")
                excitation_winding_id = None
                applied_voltage = None

        self._excitation_winding_id      = excitation_winding_id
        self._applied_voltage            = applied_voltage
        self._nominal_excitation_voltage = nominal_excitation
        self._ratio_factor = (
            self._ratio_engine.compute_ratio(applied_voltage, nominal_excitation)
            if applied_voltage and nominal_excitation else None
        )

        self._state.set_excitation(excitation_winding_id, applied_voltage, nominal_excitation)

        # Reset control events for fresh run
        self._stop_event.clear()
        self._paused.set()
        self._manual_step_event.clear()

        self._cmd_queue.put(("START", {"config": config, "operator": operator}))
        self._ensure_thread()

    def stop(self) -> None:
        self._stop_event.set()
        self._paused.set()            # unblock paused wait
        self._manual_step_event.set() # unblock manual step wait
        self._cmd_queue.put(("STOP", {}))

    def pause(self) -> None:
        if self._state.app_state == AppState.TESTING:
            self._paused.clear()
            self._state.app_state = AppState.PAUSED
            self._state.fire_test_paused()
            self._log.info("Test paused")

    def resume(self) -> None:
        if self._state.app_state == AppState.PAUSED:
            self._paused.set()
            self._state.app_state = AppState.TESTING
            self._state.fire_test_resumed()
            self._log.info("Test resumed")

    def next_step(self) -> None:
        self._manual_step_event.set()

    def next_unit(self) -> None:
        if self._state.app_state in (AppState.PASS, AppState.FAIL):
            self._cmd_queue.put(("NEXT_UNIT", {}))

    def skip_unit(self, reason: str = "") -> None:
        self._cmd_queue.put(("SKIP_UNIT", {"reason": reason or "Operator skipped"}))

    def retry_unit(self) -> None:
        if self._state.app_state in (AppState.PASS, AppState.FAIL):
            self._cmd_queue.put(("RETRY_UNIT", {}))

    def complete_batch(self) -> None:
        self._cmd_queue.put(("COMPLETE_BATCH", {}))

    def emergency_stop(self) -> None:
        self._stop_event.set()
        self._paused.set()
        self._manual_step_event.set()
        self._hw.emergency_stop()
        self._state.app_state = AppState.EMERGENCY
        self._state.fire_estop()
        self._log.error("EMERGENCY STOP — all relays opened")

    # ── Thread management ─────────────────────────────────────────────────────

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="TestEngine"
            )
            self._thread.start()

    # ── Main engine loop ──────────────────────────────────────────────────────

    def _run(self) -> None:
        """
        Stays alive between units, processing batch commands.
        Exits only on STOP, COMPLETE_BATCH, or EMERGENCY.
        """
        while True:
            try:
                cmd, payload = self._cmd_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if cmd == "START":
                self._current_config   = payload["config"]
                self._current_operator = payload["operator"]
                self._state.begin_batch(
                    self._current_config.transformer_id,
                    self._current_operator,
                )
                stopped = self._run_unit()
                if stopped:
                    break

            elif cmd == "NEXT_UNIT":
                if (self._current_config and
                        self._state.app_state in (AppState.PASS, AppState.FAIL)):
                    stopped = self._run_unit()
                    if stopped:
                        break

            elif cmd == "RETRY_UNIT":
                if (self._current_config and
                        self._state.app_state in (AppState.PASS, AppState.FAIL)):
                    stopped = self._run_unit()
                    if stopped:
                        break

            elif cmd == "SKIP_UNIT":
                self._do_skip_unit(payload.get("reason", "Operator skipped"))

            elif cmd == "COMPLETE_BATCH":
                self._do_complete_batch()
                break

            elif cmd == "STOP":
                self._do_stop()
                break

            elif cmd == "NEXT_STEP":
                self._manual_step_event.set()

    # ── Unit execution ────────────────────────────────────────────────────────

    def _run_unit(self) -> bool:
        """
        Execute one complete unit test.
        Returns True if stopped mid-run, False if it completed normally.
        """
        config   = self._current_config
        operator = self._current_operator

        steps = self._seq.load(
            config,
            excitation_winding_id=self._excitation_winding_id,
        )
        total = len(steps)

        self._state.begin_session(config.transformer_id, operator, total)
        self._state.app_state = AppState.TESTING
        self._log.info(
            f"Unit start: {config.name} ({total} steps) — Operator: {operator}"
        )

        hw_health = self._hw.health_check()
        for subsystem, status in hw_health.items():
            if status != HardwareStatus.CONNECTED:
                self._log.warn(
                    f"Hardware '{subsystem}' not connected — simulation mode"
                )

        all_pass = True

        for step in steps:
            # Stop check at step boundary
            if self._stop_event.is_set():
                self._do_stop()
                return True

            # Pause: block until resumed (or stopped)
            self._paused.wait()
            if self._stop_event.is_set():
                self._do_stop()
                return True

            result = self._execute_step(step, total)
            if not result.passed:
                all_pass = False

            if self._state.test_mode == TestMode.AUTO:
                # Inter-step gap — interruptible by stop
                deadline = time.time() + 0.4
                while time.time() < deadline:
                    if self._stop_event.is_set():
                        self._do_stop()
                        return True
                    time.sleep(0.05)
            else:
                # MANUAL mode: wait for operator's NEXT STEP
                self._manual_step_event.clear()
                while not self._manual_step_event.is_set():
                    if self._stop_event.is_set():
                        self._do_stop()
                        return True
                    time.sleep(0.05)
                self._manual_step_event.clear()

        # ── Unit completed normally ──────────────────────────────────────────
        session = self._state.end_session(all_pass)

        # Collect failed steps for the unit_completed event payload
        failed_steps = []
        if session:
            for r in session.results:
                if not r.passed:
                    failed_steps.append({
                        "from": r.from_winding,
                        "to":   r.to_winding,
                        "measured":  r.measured_voltage,
                        "expected":  r.expected_voltage,
                    })

        # Build unit result
        batch = self._state.batch_session
        unit_num = (batch.active_unit_number if batch else 1)
        unit_result = UnitResult(
            unit_number  = unit_num,
            transformer_id = config.transformer_id,
            passed       = all_pass,
            skipped      = False,
            skip_reason  = "",
            start_time   = session.start_time if session else time.time(),
            end_time     = time.time(),
            passed_steps = session.passed_steps if session else 0,
            total_steps  = session.total_steps  if session else total,
        )
        self._state.add_unit_result(unit_result)

        # Clear relays — safe state while operator swaps transformer
        self._hw.relays.reset_all_relays()
        self._state.reset_relay_states()
        self._state.fire_relays_cleared()

        # Transition to PASS / FAIL — broadcaster emits unit_completed via add_unit_result
        final_state = AppState.PASS if all_pass else AppState.FAIL
        self._state.app_state = final_state

        # Persist log (CSV/JSON export) + database (queryable, restart-safe)
        if session:
            try:
                self._log.save_session(session, operator)
            except Exception as e:
                self._log.warn(f"Log save failed: {e}")
        self._persist_unit(config, session, unit_result)

        verdict = "PASS" if all_pass else "FAIL"
        self._log.info(
            f"Unit {unit_num} complete — {verdict} "
            f"({unit_result.passed_steps}/{unit_result.total_steps} steps)"
        )
        return False

    def _execute_step(self, step: ExecutableStep, total: int) -> TestStepResult:
        # Compute expected voltage — static for legacy steps, dynamic for ratio steps
        if step.is_ratio_step and self._ratio_factor is not None:
            expected_voltage = self._ratio_engine.compute_expected(
                step.nominal_output_voltage, self._ratio_factor
            )
        elif step.is_ratio_step:
            # No applied voltage entered — use nominal as expected (unity ratio assumed)
            expected_voltage = step.nominal_output_voltage
        else:
            expected_voltage = step.expected_voltage

        self._log.step(f"Step {step.index+1}/{total}: {step.description}")

        self._state.set_test_step(
            step.index, total,
            step.from_winding_id, step.to_winding_id,
            expected_voltage, step.tolerance_percent,
            from_tap_index=step.from_tap_index,
            to_tap_index=step.to_tap_index,
            nominal_output_voltage=step.nominal_output_voltage if step.is_ratio_step else None,
            is_ratio_step=step.is_ratio_step,
        )

        # Break-before-make
        self._hw.relays.reset_all_relays()
        time.sleep(0.05)

        ok = self._hw.relays.set_all_relays(step.relay_map)
        if not ok:
            self._log.warn(f"Relay set not fully acked for step {step.index + 1}")
        self._state.set_relay_states(step.relay_map)

        # Keep the relays energized for the measurement window (≥ 2 s).
        hold_s = max(step.stabilization_delay_ms / 1000.0, MEASUREMENT_RELAY_HOLD_S)
        time.sleep(hold_s)

        reading = self._hw.voltmeter.read_voltage(step.measurement_channel)

        # Winding-polarity (phase) check — while the winding is still energized.
        # Only for a MAIN winding's full measurement (energizing↔main); taps and
        # the energizing winding itself are excluded. True=in-phase, False=out-of-
        # phase (fault), None=not checked/unavailable.
        phase_ok = self._read_step_phase(step)

        if not reading.valid:
            self._state.set_measurement(None)
            result = TestStepResult(
                step_index        = step.index,
                from_winding      = step.from_winding_id,
                to_winding        = step.to_winding_id,
                measured_voltage  = 0.0,
                expected_voltage  = expected_voltage,
                tolerance_percent = step.tolerance_percent,
                passed            = False,
                timestamp         = time.time(),
                error             = reading.error or "Voltage read error",
                phase_ok          = phase_ok,
                to_tap_index      = step.to_tap_index,
            )
            self._log.error(f"  Measurement error: {reading.error}")
        else:
            measured = reading.voltage
            self._state.set_measurement(measured)

            if step.is_ratio_step:
                passed, reason = self._ratio_engine.validate_measurement(
                    measured, expected_voltage,
                    step.tolerance_percent, step.minimum_absolute_delta
                )
            else:
                tol_v  = expected_voltage * (step.tolerance_percent / 100.0)
                passed = abs(measured - expected_voltage) <= tol_v
                reason = "PASS" if passed else "FAIL"

            # A reversed winding reads the right RMS magnitude, so an out-of-phase
            # result must fail the step independently of the voltage tolerance.
            phase_err = None
            if phase_ok is False:
                passed = False
                reason = "FAIL (out-of-phase)"
                phase_err = "Out-of-phase (winding polarity reversed)"

            result = TestStepResult(
                step_index        = step.index,
                from_winding      = step.from_winding_id,
                to_winding        = step.to_winding_id,
                measured_voltage  = measured,
                expected_voltage  = expected_voltage,
                tolerance_percent = step.tolerance_percent,
                passed            = passed,
                timestamp         = time.time(),
                error             = phase_err,
                phase_ok          = phase_ok,
                to_tap_index      = step.to_tap_index,
            )
            tap_info = ""
            if step.from_tap_index is not None:
                tap_info += f"[tap_from:{step.from_tap_index}] "
            if step.to_tap_index is not None:
                tap_info += f"[tap_to:{step.to_tap_index}] "
            ratio_info = (
                f"ratio=×{self._ratio_factor:.4f} " if step.is_ratio_step and self._ratio_factor else ""
            )
            self._log.step(
                f"  {step.from_winding_id}→{step.to_winding_id} {tap_info}{ratio_info}"
                f"Measured={measured:.3f}V Expected={expected_voltage:.3f}V "
                f"Tol=±{step.tolerance_percent}% [{reason}]"
            )

        self._state.set_step_result(result)

        # Open relays after measurement
        self._hw.relays.reset_all_relays()
        self._state.reset_relay_states()

        return result

    # ── Stop / skip / complete ─────────────────────────────────────────────────

    def _do_stop(self) -> None:
        if self._state.app_state == AppState.IDLE:
            return  # idempotent
        self._hw.relays.reset_all_relays()
        self._state.reset_relay_states()
        self._state.fire_relays_cleared()
        if self._state.app_state != AppState.EMERGENCY:
            self._state.app_state = AppState.IDLE
        if self._state.batch_session is not None:
            self._state.end_batch()
        self._log.info("Test stopped — all relays opened")
        self._state.fire_test_stopped()

    def _do_skip_unit(self, reason: str) -> None:
        batch = self._state.batch_session
        unit_num = batch.active_unit_number if batch else 1
        unit_result = UnitResult(
            unit_number    = unit_num,
            transformer_id = self._current_config.transformer_id if self._current_config else "?",
            passed         = None,
            skipped        = True,
            skip_reason    = reason,
            start_time     = time.time(),
            end_time       = time.time(),
            passed_steps   = 0,
            total_steps    = 0,
        )
        self._state.add_unit_result(unit_result)
        self._persist_unit(self._current_config, None, unit_result)
        self._log.info(f"Unit {unit_num} skipped: {reason}")

    def _do_complete_batch(self) -> None:
        self._hw.relays.reset_all_relays()
        self._state.reset_relay_states()
        self._state.fire_relays_cleared()
        batch = self._state.batch_session
        if batch is not None:
            if self._store is not None:
                try:
                    self._store.close_batch(batch.batch_id, status="complete")
                except Exception as e:
                    self._log.warn(f"DB close_batch failed: {e}")
            self._state.end_batch()
        self._state.app_state = AppState.IDLE
        self._log.info("Batch completed")
        self._state.fire_test_stopped()
