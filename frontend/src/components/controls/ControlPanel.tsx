/**
 * ControlPanel — transformer selector, operator input, test controls.
 */
import { useState, useEffect } from 'react'
import { useAppStore } from '@/store/useAppStore'
import * as api from '@/api/client'

interface StateBadgeProps { state: string }
function StateBadge({ state }: StateBadgeProps) {
  const map: Record<string, string> = {
    IDLE:         'bg-muted/30 text-muted',
    INITIALIZING: 'bg-primary/30 text-accent',
    READY:        'bg-primary/30 text-accent',
    TESTING:      'bg-glow/20 text-glow animate-pulse',
    PAUSED:       'bg-warning/20 text-warning',
    PASS:         'bg-success/20 text-success',
    FAIL:         'bg-danger/20 text-danger',
    ERROR:        'bg-danger/20 text-danger',
    EMERGENCY:    'bg-danger text-white',
  }
  return (
    <span className={`text-xs font-mono px-3 py-1 rounded-full font-semibold tracking-wider ${map[state] ?? 'text-muted'}`}>
      {state}
    </span>
  )
}

export function ControlPanel() {
  const {
    appState, testMode, selectedTransformerId,
    operator, transformerList, progressPct,
    currentStepIndex, totalSteps, wsConnected,
    setOperator, setTestMode, setSelectedTransformer, setTransformerList,
  } = useAppStore()

  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.getTransformers()
      .then(setTransformerList)
      .catch(() => {})
  }, [setTransformerList])

  const isTesting = appState === 'TESTING'
  const isPaused  = appState === 'PAUSED'
  const isIdle    = ['IDLE', 'READY', 'PASS', 'FAIL', 'ERROR'].includes(appState)

  const handleSelectTransformer = async (id: string) => {
    setSelectedTransformer(id)
    try {
      await api.selectTransformer(id)
    } catch {}
  }

  const handleStart = async () => {
    if (!selectedTransformerId) return
    setLoading(true)
    try {
      await api.startTest(operator, testMode as 'AUTO' | 'MANUAL')
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const handleStop = () => api.stopTest().catch(() => {})
  const handlePause = () => api.pauseTest().catch(() => {})
  const handleResume = () => api.resumeTest().catch(() => {})
  const handleNext = () => api.nextStep().catch(() => {})
  const handleEmergency = () => api.emergencyStop().catch(() => {})

  return (
    <div className="bg-panel border border-border rounded-lg p-4 space-y-4">
      {/* header row */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-mono text-muted uppercase tracking-widest">
          Test Control
        </span>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-glow' : 'bg-danger'}`} />
          <span className="text-xs text-muted font-mono">
            {wsConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
      </div>

      {/* transformer selector */}
      <div className="space-y-1">
        <label className="text-xs text-muted font-mono">Transformer</label>
        <select
          className="w-full bg-card border border-border rounded px-3 py-2 text-sm font-mono text-white
                     focus:outline-none focus:border-accent"
          value={selectedTransformerId ?? ''}
          onChange={(e) => handleSelectTransformer(e.target.value)}
          disabled={isTesting || isPaused}
        >
          <option value="">— Select —</option>
          {transformerList.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
      </div>

      {/* operator */}
      <div className="space-y-1">
        <label className="text-xs text-muted font-mono">Operator</label>
        <input
          type="text"
          className="w-full bg-card border border-border rounded px-3 py-2 text-sm font-mono text-white
                     focus:outline-none focus:border-accent placeholder-muted"
          placeholder="Operator name"
          value={operator}
          onChange={(e) => setOperator(e.target.value)}
          disabled={isTesting || isPaused}
        />
      </div>

      {/* mode selector */}
      <div className="flex gap-2">
        {(['AUTO', 'MANUAL'] as const).map((m) => (
          <button
            key={m}
            className={`flex-1 py-1.5 rounded text-xs font-mono font-medium transition-colors
              ${testMode === m
                ? 'bg-primary text-white'
                : 'bg-card text-muted border border-border hover:border-accent'}`}
            onClick={() => setTestMode(m)}
            disabled={isTesting || isPaused}
          >
            {m}
          </button>
        ))}
      </div>

      {/* state badge */}
      <div className="flex justify-center">
        <StateBadge state={appState} />
      </div>

      {/* progress bar */}
      {totalSteps > 0 && (
        <div className="space-y-1">
          <div className="flex justify-between text-xs font-mono text-muted">
            <span>Step {Math.max(0, currentStepIndex + 1)} / {totalSteps}</span>
            <span>{progressPct.toFixed(0)}%</span>
          </div>
          <div className="h-2 bg-card rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-primary to-glow transition-all duration-300 rounded-full"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* control buttons */}
      <div className="grid grid-cols-2 gap-2">
        {isIdle && (
          <button
            className="col-span-2 py-2.5 rounded font-mono font-semibold text-sm
                       bg-glow text-black hover:bg-green-400 transition-colors
                       disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={handleStart}
            disabled={!selectedTransformerId || loading}
          >
            ▶ START TEST
          </button>
        )}

        {isTesting && (
          <>
            <button
              className="py-2 rounded font-mono text-sm bg-warning text-black hover:bg-yellow-400 transition-colors"
              onClick={handlePause}
            >
              ⏸ PAUSE
            </button>
            <button
              className="py-2 rounded font-mono text-sm bg-card text-muted border border-border hover:border-danger hover:text-danger transition-colors"
              onClick={handleStop}
            >
              ⏹ STOP
            </button>
          </>
        )}

        {isPaused && (
          <>
            <button
              className="py-2 rounded font-mono text-sm bg-glow text-black hover:bg-green-400 transition-colors"
              onClick={handleResume}
            >
              ▶ RESUME
            </button>
            {testMode === 'MANUAL' && (
              <button
                className="py-2 rounded font-mono text-sm bg-accent text-black hover:bg-cyan-400 transition-colors"
                onClick={handleNext}
              >
                → NEXT
              </button>
            )}
            <button
              className="col-span-2 py-2 rounded font-mono text-sm bg-card text-muted border border-border hover:border-danger hover:text-danger"
              onClick={handleStop}
            >
              ⏹ STOP
            </button>
          </>
        )}
      </div>

      {/* emergency stop */}
      <button
        className="w-full py-2 rounded font-mono font-bold text-sm
                   bg-danger/10 text-danger border border-danger/30
                   hover:bg-danger hover:text-white transition-colors"
        onClick={handleEmergency}
      >
        ⚡ EMERGENCY STOP
      </button>
    </div>
  )
}
