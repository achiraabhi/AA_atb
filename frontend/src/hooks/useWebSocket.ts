import { useEffect, useRef, useCallback } from 'react'
import { useAppStore } from '@/store/useAppStore'
import type {
  WsEvent, AppSnapshot,
  RelayStateChangedData, VoltageUpdatedData,
  ActiveMeasChangedData, TestProgressData,
  AnimationStateData, StepResult, TestSession,
} from '@/types'

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
const RECONNECT_DELAY_MS = 2500

export function useWebSocket() {
  const wsRef        = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef   = useRef(true)

  const store = useAppStore()

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      store.setConnected(true)
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current)
        reconnectRef.current = null
      }
    }

    ws.onclose = () => {
      store.setConnected(false)
      wsRef.current = null
      if (mountedRef.current) {
        reconnectRef.current = setTimeout(connect, RECONNECT_DELAY_MS)
      }
    }

    ws.onerror = () => {
      // onclose fires after onerror — reconnect handled there
    }

    ws.onmessage = (ev) => {
      let msg: WsEvent
      try {
        msg = JSON.parse(ev.data as string) as WsEvent
      } catch {
        return
      }
      dispatch(msg, store)
    }
  }, [store])

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  const send = useCallback((event: { type: string; data?: unknown }) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(event))
    }
  }, [])

  return { send, connected: store.wsConnected }
}

// ── event dispatcher ────────────────────────────────────────────────────────

type Store = ReturnType<typeof useAppStore>

function dispatch(msg: WsEvent, store: Store) {
  switch (msg.type) {
    case 'snapshot': {
      const d = msg.data as AppSnapshot
      store.applySnapshot(d)
      break
    }
    case 'app_state': {
      const d = msg.data as { state: string }
      store.setAppState(d.state as never)
      break
    }
    case 'relay_state_changed': {
      const d = msg.data as RelayStateChangedData
      store.setRelayStates(d.relays)
      break
    }
    case 'voltage_updated': {
      const d = msg.data as VoltageUpdatedData
      store.setCurrentVoltage(d.voltage)
      break
    }
    case 'active_measurement_changed': {
      const d = msg.data as ActiveMeasChangedData
      store.setActiveMeasurement(d)
      break
    }
    case 'test_progress': {
      const d = msg.data as TestProgressData
      store.setProgress(d.step_index, d.total, d.progress_pct)
      break
    }
    case 'step_result': {
      const d = msg.data as StepResult
      store.addStepResult(d)
      break
    }
    case 'session_started': {
      const d = msg.data as TestSession
      store.setSession(d)
      break
    }
    case 'session_ended': {
      const d = msg.data as { overall_pass: boolean; total_steps: number; passed_steps: number }
      store.endSession(d.overall_pass)
      break
    }
    case 'animation_state': {
      const d = msg.data as AnimationStateData
      store.setAnimation(d.active_primary, d.active_secondary)
      break
    }
    case 'error': {
      const d = msg.data as { message: string }
      store.setError(d.message)
      break
    }
    case 'reset':
      store.reset()
      break
  }
}
