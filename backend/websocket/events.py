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

# Client → Server commands (received over WS as an alternative to REST)
CMD_START_TEST            = "start_test"
CMD_STOP_TEST             = "stop_test"
CMD_PAUSE_TEST            = "pause_test"
CMD_RESUME_TEST           = "resume_test"
CMD_NEXT_STEP             = "next_step"
CMD_EMERGENCY_STOP        = "emergency_stop"
CMD_SELECT_TRANSFORMER    = "select_transformer"
CMD_SET_OPERATOR          = "set_operator"
