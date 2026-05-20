// ── transformer topology ───────────────────────────────────────────────────

export interface TapConfig {
  pin: number
  voltage: number
  label?: string
  relay_a?: number | null
  relay_b?: number | null
  meas_channel?: number
}

export interface WindingConfig {
  id: string
  winding_type?: string
  start_pin: number
  end_pin: number
  voltage: number
  dot_polarity?: boolean
  relay_a?: number | null
  relay_b?: number | null
  meas_channel?: number
  taps: TapConfig[]
  coords?: Record<string, number>
}

export interface AutoMatrixConfig {
  enabled: boolean
  energize_winding: string
  energize_tap_index?: number | null
}

export interface TransformerConfig {
  name: string
  transformer_id: string
  type?: string
  rated_power_va?: number
  rated_frequency_hz?: number
  notes?: string
  primary: WindingConfig[]
  secondary: WindingConfig[]
  tests: TestStep[]
  auto_matrix?: AutoMatrixConfig
}

export interface TestStep {
  from: string
  to: string
  expected_voltage: number
  tolerance_percent?: number
  measurement_channel?: number
  stabilization_delay_ms?: number
  description?: string
  from_tap_index?: number | null
  to_tap_index?: number | null
}

export interface TransformerSummary {
  transformer_id: string
  name: string
  type: string
  rated_power_va: number
  primary_count: number
  secondary_count: number
  auto_matrix: boolean
}

// ── test state ─────────────────────────────────────────────────────────────

export type AppStateValue =
  | 'IDLE' | 'INITIALIZING' | 'READY' | 'TESTING' | 'PAUSED'
  | 'PASS' | 'FAIL' | 'ERROR' | 'EMERGENCY'

export type TestModeValue = 'AUTO' | 'MANUAL'

export interface StepResult {
  step_index: number
  from_winding: string
  to_winding: string
  measured_voltage: number
  expected_voltage: number
  tolerance_pct: number
  passed: boolean
  timestamp: number
  error?: string | null
}

export interface TestSession {
  transformer_id: string
  operator: string
  start_time: number
  total_steps: number
  passed_steps: number
  overall_pass?: boolean | null
}

export interface AppSnapshot {
  app_state: AppStateValue
  test_mode: TestModeValue
  selected_transformer: string | null
  operator: string
  relay_states: Record<string, boolean>
  current_step: number
  total_steps: number
  progress_pct: number
  transformers: { id: string; name: string }[]
}

// ── WebSocket events ────────────────────────────────────────────────────────

export interface WsEvent<T = unknown> {
  type: string
  data: T
}

export interface RelayStateChangedData {
  relays: Record<string, boolean>
}

export interface VoltageUpdatedData {
  voltage: number
  channel: number
}

export interface ActiveMeasChangedData {
  from_winding: string
  to_winding: string
  expected_voltage: number
  tolerance_pct: number
  from_tap_index?: number | null
  to_tap_index?: number | null
}

export interface TestProgressData {
  step_index: number
  total: number
  progress_pct: number
}

export interface AnimationStateData {
  active_primary: string | null
  active_secondary: string | null
}

// ── hardware ────────────────────────────────────────────────────────────────

export interface HardwareStatus {
  relay_controller: string
  voltmeter: string
}
