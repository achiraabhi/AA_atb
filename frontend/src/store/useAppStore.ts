import { create } from 'zustand'
import type {
  AppStateValue, TestModeValue, StepResult, TestSession,
  TransformerSummary, TransformerConfig, AppSnapshot,
  ActiveMeasChangedData,
} from '@/types'

interface AppStore {
  // connection
  wsConnected: boolean

  // transformer catalogue
  transformerList: { id: string; name: string }[]
  transformerSummaries: TransformerSummary[]
  selectedTransformerId: string | null
  loadedConfig: TransformerConfig | null

  // operator
  operator: string

  // test run state
  appState: AppStateValue
  testMode: TestModeValue
  currentStepIndex: number
  totalSteps: number
  progressPct: number

  // active measurement
  activePrimary: string | null
  activeSecondary: string | null
  currentVoltage: number | null
  expectedVoltage: number | null
  tolerancePct: number
  fromTapIndex: number | null
  toTapIndex: number | null

  // relay grid
  relayStates: Record<string, boolean>

  // results
  stepResults: StepResult[]
  session: TestSession | null

  // animation
  animActivePrimary: string | null
  animActiveSecondary: string | null

  // error
  errorMessage: string | null

  // ── actions ──────────────────────────────────────────────────────────────
  setConnected: (v: boolean) => void
  applySnapshot: (snap: AppSnapshot) => void
  setAppState: (s: AppStateValue) => void
  setRelayStates: (r: Record<string, boolean>) => void
  setCurrentVoltage: (v: number) => void
  setActiveMeasurement: (d: ActiveMeasChangedData) => void
  setProgress: (step: number, total: number, pct: number) => void
  addStepResult: (r: StepResult) => void
  setSession: (s: TestSession) => void
  endSession: (pass: boolean) => void
  setAnimation: (primary: string | null, secondary: string | null) => void
  setError: (msg: string) => void
  reset: () => void

  setOperator: (op: string) => void
  setTestMode: (mode: TestModeValue) => void
  setSelectedTransformer: (id: string | null) => void
  setLoadedConfig: (cfg: TransformerConfig | null) => void
  setTransformerList: (list: TransformerSummary[]) => void
}

export const useAppStore = create<AppStore>((set) => ({
  wsConnected: false,
  transformerList: [],
  transformerSummaries: [],
  selectedTransformerId: null,
  loadedConfig: null,
  operator: '',
  appState: 'IDLE',
  testMode: 'AUTO',
  currentStepIndex: -1,
  totalSteps: 0,
  progressPct: 0,
  activePrimary: null,
  activeSecondary: null,
  currentVoltage: null,
  expectedVoltage: null,
  tolerancePct: 5,
  fromTapIndex: null,
  toTapIndex: null,
  relayStates: {},
  stepResults: [],
  session: null,
  animActivePrimary: null,
  animActiveSecondary: null,
  errorMessage: null,

  setConnected: (v) => set({ wsConnected: v }),

  applySnapshot: (snap) => set({
    appState:             snap.app_state,
    testMode:             snap.test_mode,
    selectedTransformerId: snap.selected_transformer,
    operator:             snap.operator,
    relayStates:          snap.relay_states,
    currentStepIndex:     snap.current_step,
    totalSteps:           snap.total_steps,
    progressPct:          snap.progress_pct,
    transformerList:      snap.transformers,
  }),

  setAppState: (s) => set({ appState: s }),

  setRelayStates: (r) => set({ relayStates: r }),

  setCurrentVoltage: (v) => set({ currentVoltage: v }),

  setActiveMeasurement: (d) => set({
    activePrimary:   d.from_winding,
    activeSecondary: d.to_winding,
    expectedVoltage: d.expected_voltage,
    tolerancePct:    d.tolerance_pct,
    fromTapIndex:    d.from_tap_index ?? null,
    toTapIndex:      d.to_tap_index ?? null,
  }),

  setProgress: (step, total, pct) => set({
    currentStepIndex: step,
    totalSteps:       total,
    progressPct:      pct,
  }),

  addStepResult: (r) => set((s) => ({
    stepResults: [...s.stepResults, r],
  })),

  setSession: (s) => set({
    session:     s,
    stepResults: [],
  }),

  endSession: (pass) => set((s) => ({
    session: s.session ? { ...s.session, overall_pass: pass } : null,
  })),

  setAnimation: (primary, secondary) => set({
    animActivePrimary:   primary,
    animActiveSecondary: secondary,
  }),

  setError: (msg) => set({ errorMessage: msg }),

  reset: () => set({
    activePrimary:    null,
    activeSecondary:  null,
    currentVoltage:   null,
    expectedVoltage:  null,
    progressPct:      0,
    currentStepIndex: -1,
    relayStates:      {},
    errorMessage:     null,
    animActivePrimary: null,
    animActiveSecondary: null,
  }),

  setOperator: (op) => set({ operator: op }),
  setTestMode: (mode) => set({ testMode: mode }),
  setSelectedTransformer: (id) => set({ selectedTransformerId: id }),
  setLoadedConfig: (cfg) => set({ loadedConfig: cfg }),
  setTransformerList: (list) => set({
    transformerSummaries: list,
    transformerList: list.map((t) => ({ id: t.transformer_id, name: t.name })),
  }),
}))
