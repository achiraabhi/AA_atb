"""
StateManager → WebSocket bridge.
Subscribes to all StateManager keys and forwards events to connected WebSocket clients.
Handles the sync→async boundary: StateManager callbacks run in background threads;
we use run_coroutine_threadsafe to post to the asyncio event loop.
"""
import asyncio
import logging
from typing import Optional

from core.state_manager import StateManager, AppState, TestStepResult, TestSession
from backend.websocket.manager import WebSocketManager
from backend.websocket.events import (
    EVT_APP_STATE, EVT_RELAY_STATE_CHANGED, EVT_VOLTAGE_UPDATED,
    EVT_ACTIVE_MEAS_CHANGED, EVT_TEST_PROGRESS, EVT_STEP_RESULT,
    EVT_SESSION_STARTED, EVT_SESSION_ENDED, EVT_ERROR, EVT_RESET,
    EVT_ANIMATION_STATE,
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

    # ── helpers ─────────────────────────────────────────────────────────────

    def _send(self, event: dict) -> None:
        self._ws.broadcast_sync(event, self._loop)

    # ── handlers ────────────────────────────────────────────────────────────

    def _on_app_state(self, value: AppState) -> None:
        self._send({
            "type": EVT_APP_STATE,
            "data": {"state": value.value}
        })

    def _on_relay_states(self, states: dict) -> None:
        self._send({
            "type": EVT_RELAY_STATE_CHANGED,
            "data": {"relays": {str(k): v for k, v in states.items()}}
        })

    def _on_measurement(self, voltage: float) -> None:
        self._send({
            "type": EVT_VOLTAGE_UPDATED,
            "data": {"voltage": voltage, "channel": 0}
        })

    def _on_test_step(self, step_info: dict) -> None:
        sm = self._sm
        self._send({
            "type": EVT_ACTIVE_MEAS_CHANGED,
            "data": {
                "from_winding":     step_info["from_w"],
                "to_winding":       step_info["to_w"],
                "expected_voltage": step_info["expected"],
                "tolerance_pct":    step_info["tolerance"],
                "from_tap_index":   step_info.get("from_tap_index"),
                "to_tap_index":     step_info.get("to_tap_index"),
            }
        })
        self._send({
            "type": EVT_TEST_PROGRESS,
            "data": {
                "step_index":   step_info["step_index"],
                "total":        step_info["total"],
                "progress_pct": (step_info["step_index"] + 1) / step_info["total"] * 100
                                if step_info["total"] else 0,
            }
        })
        # animation state mirrors active winding IDs
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
        self._send({
            "type": EVT_ERROR,
            "data": {"message": message}
        })

    def _on_reset(self, _: None) -> None:
        self._send({"type": EVT_RESET, "data": {}})
