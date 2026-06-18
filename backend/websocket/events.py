"""
WebSocket event type constants shared between broadcaster and frontend.
"""

# Server → Client events
EVT_APP_STATE             = "app_state"
EVT_RELAY_STATE_CHANGED   = "relay_state_changed"
EVT_VOLTAGE_UPDATED       = "voltage_updated"
EVT_ACTIVE_MEAS_CHANGED   = "active_measurement_changed"
EVT_TEST_PROGRESS         = "test_progress"
EVT_STEP_RESULT           = "step_result"
EVT_SESSION_STARTED       = "session_started"
EVT_SESSION_ENDED         = "session_ended"
EVT_VALIDATION_RESULT     = "validation_result"
EVT_ANIMATION_STATE       = "animation_state"
EVT_ERROR                 = "error"
EVT_RESET                 = "reset"

# Batch / unit lifecycle events
EVT_BATCH_STARTED         = "batch_started"
EVT_UNIT_STARTED          = "unit_started"
EVT_UNIT_COMPLETED        = "unit_completed"
EVT_UNIT_SKIPPED          = "unit_skipped"
EVT_TEST_PAUSED           = "test_paused"
EVT_TEST_RESUMED          = "test_resumed"
EVT_TEST_STOPPED          = "test_stopped"
EVT_ESTOP_TRIGGERED       = "estop_triggered"
EVT_RELAYS_CLEARED        = "relays_cleared"
EVT_BATCH_SUMMARY         = "batch_summary"

EVT_EXCITATION_CONFIG     = "excitation_config"   # broadcasts ratio params
EVT_LIVE_VOLTAGES         = "live_voltages"        # V1/V2 from serial meters, every second

# Client → Server commands (received over WS as an alternative to REST)
CMD_START_TEST            = "start_test"
CMD_STOP_TEST             = "stop_test"
CMD_PAUSE_TEST            = "pause_test"
CMD_RESUME_TEST           = "resume_test"
CMD_NEXT_STEP             = "next_step"
CMD_NEXT_UNIT             = "next_unit"
CMD_SKIP_UNIT             = "skip_unit"
CMD_RETRY_UNIT            = "retry_unit"
CMD_COMPLETE_BATCH        = "complete_batch"
CMD_EMERGENCY_STOP        = "emergency_stop"
CMD_SELECT_TRANSFORMER    = "select_transformer"
CMD_SET_OPERATOR          = "set_operator"
CMD_SET_EXCITATION        = "set_excitation"   # { excitation_winding_id, applied_voltage }
