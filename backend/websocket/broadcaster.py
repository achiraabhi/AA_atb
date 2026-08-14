"""
StateManager → WebSocket bridge.
Subscribes to all StateManager keys and forwards events to connected WebSocket clients.
Handles the sync→async boundary: StateManager callbacks run in background threads;
we use run_coroutine_threadsafe to post to the asyncio event loop.
"""
import asyncio
import logging
from typing import Optional

from core.state_manager import StateManager, AppState, TestStepResult, TestSession, UnitResult
from backend.websocket.manager import WebSocketManager
from backend.websocket.events import (
    EVT_APP_STATE, EVT_RELAY_STATE_CHANGED, EVT_VOLTAGE_UPDATED,
    EVT_ACTIVE_MEAS_CHANGED, EVT_TEST_PROGRESS, EVT_STEP_RESULT,
    EVT_SESSION_STARTED, EVT_SESSION_ENDED, EVT_ERROR, EVT_RESET,
    EVT_ANIMATION_STATE, EVT_EXCITATION_CONFIG,
    EVT_BATCH_SUMMARY, EVT_UNIT_COMPLETED, EVT_UNIT_SKIPPED,
    EVT_TEST_PAUSED, EVT_TEST_RESUMED, EVT_TEST_STOPPED,
    EVT_ESTOP_TRIGGERED, EVT_RELAYS_CLEARED,
)

log = logging.getLogger(__name__)


class WsBroadcaster:
    """
    Wires StateManager observer callbacks to WebSocket broadcasts.
    Must be created after the asyncio event loop is running so that
    run_coroutine_threadsafe has a live loop to post to.
    """

    def __init__(self, state_manager: StateManager,
                 ws_manager: WebSocketManager,
                 loop: asyncio.AbstractEventLoop) -> None:
        self._ws   = ws_manager
        self._loop = loop
        self._sm   = state_manager

        state_manager.subscribe("app_state",        self._on_app_state)
        state_manager.subscribe("relay_states",     self._on_relay_states)
        state_manager.subscribe("measurement",      self._on_measurement)
        state_manager.subscribe("test_step",        self._on_test_step)
        state_manager.subscribe("step_result",      self._on_step_result)
        state_manager.subscribe("session_started",  self._on_session_started)
        state_manager.subscribe("session_ended",    self._on_session_ended)
        state_manager.subscribe("error",            self._on_error)
        state_manager.subscribe("reset",            self._on_reset)
        state_manager.subscribe("excitation_config", self._on_excitation_config)
        # Batch / unit events
        state_manager.subscribe("batch_state",     self._on_batch_state)
        state_manager.subscribe("unit_completed",  self._on_unit_completed)
        state_manager.subscribe("unit_skipped",    self._on_unit_skipped)
        state_manager.subscribe("test_paused",     self._on_test_paused)
        state_manager.subscribe("test_resumed",    self._on_test_resumed)
        state_manager.subscribe("test_stopped",    self._on_test_stopped)
        state_manager.subscribe("estop_triggered", self._on_estop)
        state_manager.subscribe("relays_cleared",  self._on_relays_cleared)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _send(self, event: dict) -> None:
        self._ws.broadcast_sync(event, self._loop)

    # ── core state handlers ──────────────────────────────────────────────────

    def _on_app_state(self, value: AppState) -> None:
        self._send({"type": EVT_APP_STATE, "data": {"state": value.value}})

    def _on_relay_states(self, states: dict) -> None:
        self._send({
            "type": EVT_RELAY_STATE_CHANGED,
            "data": {"relays": {str(k): v for k, v in states.items()}}
        })

    def _on_measurement(self, voltage) -> None:
        self._send({"type": EVT_VOLTAGE_UPDATED, "data": {"voltage": voltage, "no_signal": voltage is None, "channel": 0}})

    def _on_excitation_config(self, info: dict) -> None:
        self._send({"type": EVT_EXCITATION_CONFIG, "data": info})

    def _on_test_step(self, step_info: dict) -> None:
        self._send({
            "type": EVT_ACTIVE_MEAS_CHANGED,
            "data": {
                "from_winding":           step_info["from_w"],
                "to_winding":             step_info["to_w"],
                "expected_voltage":       step_info["expected"],
                "tolerance_pct":          step_info["tolerance"],
                "from_tap_index":         step_info.get("from_tap_index"),
                "to_tap_index":           step_info.get("to_tap_index"),
                "nominal_output_voltage": step_info.get("nominal_output_voltage"),
                "ratio_factor":           step_info.get("ratio_factor"),
                "is_ratio_step":          step_info.get("is_ratio_step", False),
            }
        })
        total = step_info["total"]
        idx   = step_info["step_index"]
        self._send({
            "type": EVT_TEST_PROGRESS,
            "data": {
                "step_index":   idx,
                "total":        total,
                "progress_pct": (idx + 1) / total * 100 if total else 0,
            }
        })
        self._send({
            "type": EVT_ANIMATION_STATE,
            "data": {
                "active_primary":   step_info["from_w"],
                "active_secondary": step_info["to_w"],
            }
        })

    def _on_step_result(self, result: TestStepResult) -> None:
        self._send({
            "type": EVT_STEP_RESULT,
            "data": {
                "step_index":       result.step_index,
                "from_winding":     result.from_winding,
                "to_winding":       result.to_winding,
                "measured_voltage": result.measured_voltage,
                "expected_voltage": result.expected_voltage,
                "tolerance_pct":    result.tolerance_percent,
                "passed":           result.passed,
                "phase_ok":         result.phase_ok,
                "to_tap_index":     result.to_tap_index,
                "timestamp":        result.timestamp,
                "error":            result.error,
            }
        })

    def _on_session_started(self, session: Optional[TestSession]) -> None:
        if session:
            self._send({
                "type": EVT_SESSION_STARTED,
                "data": {
                    "transformer_id": session.transformer_id,
                    "operator":       session.operator,
                    "start_time":     session.start_time,
                }
            })

    def _on_session_ended(self, session: Optional[TestSession]) -> None:
        if session:
            self._send({
                "type": EVT_SESSION_ENDED,
                "data": {
                    "transformer_id": session.transformer_id,
                    "overall_pass":   session.overall_pass,
                    "total_steps":    session.total_steps,
                    "passed_steps":   session.passed_steps,
                }
            })

    def _on_error(self, message: str) -> None:
        self._send({"type": EVT_ERROR, "data": {"message": message}})

    def _on_reset(self, _: None) -> None:
        self._send({"type": EVT_RESET, "data": {}})

    # ── batch / unit handlers ────────────────────────────────────────────────

    def _on_batch_state(self, summary: dict) -> None:
        self._send({"type": EVT_BATCH_SUMMARY, "data": summary})

    def _on_unit_completed(self, unit: UnitResult) -> None:
        # Collect failed step data from the current session if available
        session = self._sm.current_session
        failed_steps = []
        if session:
            for r in session.results:
                if not r.passed:
                    failed_steps.append({
                        "from":     r.from_winding,
                        "to":       r.to_winding,
                        "measured": r.measured_voltage,
                        "expected": r.expected_voltage,
                    })
        batch = self._sm.batch_session
        self._send({
            "type": EVT_UNIT_COMPLETED,
            "data": {
                "unit_number":   unit.unit_number,
                "transformer_id": unit.transformer_id,
                "overall_pass":  unit.passed,
                "passed_steps":  unit.passed_steps,
                "total_steps":   unit.total_steps,
                "duration":      unit.duration,
                "failures":      failed_steps,
                "batch": batch.to_summary(active=True) if batch else None,
            }
        })

    def _on_unit_skipped(self, unit: UnitResult) -> None:
        batch = self._sm.batch_session
        self._send({
            "type": EVT_UNIT_SKIPPED,
            "data": {
                "unit_number": unit.unit_number,
                "reason":      unit.skip_reason,
                "batch": batch.to_summary(active=True) if batch else None,
            }
        })

    def _on_test_paused(self, _: None) -> None:
        self._send({"type": EVT_TEST_PAUSED, "data": {}})

    def _on_test_resumed(self, _: None) -> None:
        self._send({"type": EVT_TEST_RESUMED, "data": {}})

    def _on_test_stopped(self, _: None) -> None:
        self._send({"type": EVT_TEST_STOPPED, "data": {}})

    def _on_estop(self, _: None) -> None:
        self._send({"type": EVT_ESTOP_TRIGGERED, "data": {}})

    def _on_relays_cleared(self, _: None) -> None:
        self._send({"type": EVT_RELAYS_CLEARED, "data": {}})
