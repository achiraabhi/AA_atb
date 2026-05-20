import type {
  TransformerConfig, TransformerSummary, AppSnapshot, StepResult,
} from '@/types'

const BASE = '/api'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText)
    throw new Error(`API ${path}: ${res.status} — ${err}`)
  }
  return res.json() as Promise<T>
}

// ── transformer catalogue ──────────────────────────────────────────────────

export const getTransformers = () =>
  req<TransformerSummary[]>('/transformers')

export const getTransformer = (id: string) =>
  req<TransformerConfig>(`/transformers/${id}`)

export const saveTransformer = (data: TransformerConfig, filename?: string) =>
  req<{ saved: boolean; path: string }>('/transformers', {
    method: 'POST',
    body: JSON.stringify({ data, filename }),
  })

export const deleteTransformer = (id: string) =>
  req<{ deleted: boolean }>(`/transformers/${id}`, { method: 'DELETE' })

// ── state & control ────────────────────────────────────────────────────────

export const getState = () => req<AppSnapshot>('/state')

export const getRelays = () => req<{ relays: Record<string, boolean> }>('/relays')

export const selectTransformer = (transformer_id: string) =>
  req<{ selected: string }>('/transformer/select', {
    method: 'POST',
    body: JSON.stringify({ transformer_id }),
  })

export const startTest = (operator = '', mode: 'AUTO' | 'MANUAL' = 'AUTO') =>
  req<{ started: boolean }>('/test/start', {
    method: 'POST',
    body: JSON.stringify({ operator, mode }),
  })

export const stopTest = () =>
  req<{ stopped: boolean }>('/test/stop', { method: 'POST' })

export const pauseTest = () =>
  req<{ paused: boolean }>('/test/pause', { method: 'POST' })

export const resumeTest = () =>
  req<{ resumed: boolean }>('/test/resume', { method: 'POST' })

export const nextStep = () =>
  req<{ next: boolean }>('/test/next-step', { method: 'POST' })

export const emergencyStop = () =>
  req<{ emergency_stop: boolean }>('/test/emergency-stop', { method: 'POST' })

// ── hardware ───────────────────────────────────────────────────────────────

export const getHardwareStatus = () =>
  req<Record<string, string>>('/hardware/status')

// ── logs ───────────────────────────────────────────────────────────────────

export const getLogs = () =>
  req<{ files: { name: string; size: number; modified: number }[] }>('/logs')

export const getLog = (filename: string) =>
  req<{ filename: string; content: string }>(`/logs/${encodeURIComponent(filename)}`)
