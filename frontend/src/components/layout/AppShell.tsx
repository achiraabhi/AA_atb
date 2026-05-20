/**
 * AppShell — top-level industrial UI layout.
 * Tabs: Dashboard | Editor | Logs
 */
import { useState, useEffect } from 'react'
import { useAppStore } from '@/store/useAppStore'
import { useWebSocket } from '@/hooks/useWebSocket'
import { ControlPanel } from '@/components/controls/ControlPanel'
import { StatusPanel } from '@/components/status/StatusPanel'
import { RelayGrid } from '@/components/relay/RelayGrid'
import { TransformerCanvas } from '@/components/canvas/TransformerCanvas'
import { TransformerEditor } from '@/components/editor/TransformerEditor'
import * as api from '@/api/client'
import type { TransformerConfig } from '@/types'

type Tab = 'dashboard' | 'editor' | 'logs'

function Header({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  const { wsConnected, appState } = useAppStore()

  const stateColor: Record<string, string> = {
    IDLE:      'text-muted',
    READY:     'text-accent',
    TESTING:   'text-glow',
    PAUSED:    'text-warning',
    PASS:      'text-success',
    FAIL:      'text-danger',
    ERROR:     'text-danger',
    EMERGENCY: 'text-danger',
  }

  return (
    <header className="h-12 bg-panel border-b border-border flex items-center px-4 gap-6 shrink-0">
      {/* brand */}
      <div className="flex items-center gap-2">
        <div className="w-5 h-5 rounded bg-primary flex items-center justify-center">
          <span className="text-white text-xs font-bold">T</span>
        </div>
        <span className="font-mono font-semibold text-sm text-white tracking-wide">ATB</span>
        <span className="text-muted text-xs font-mono hidden sm:block">
          After-Assembling Test Bench
        </span>
      </div>

      {/* tabs */}
      <nav className="flex gap-1">
        {(['dashboard', 'editor', 'logs'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 rounded text-xs font-mono font-medium capitalize transition-colors
              ${tab === t
                ? 'bg-primary/20 text-white border border-primary/30'
                : 'text-muted hover:text-white'}`}
          >
            {t}
          </button>
        ))}
      </nav>

      {/* right side */}
      <div className="ml-auto flex items-center gap-4">
        <span className={`text-sm font-mono font-semibold ${stateColor[appState] ?? 'text-muted'}`}>
          {appState}
        </span>
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-glow animate-pulse' : 'bg-danger'}`} />
          <span className="text-xs font-mono text-muted">
            {wsConnected ? 'Live' : 'Offline'}
          </span>
        </div>
      </div>
    </header>
  )
}

function DashboardTab() {
  const { selectedTransformerId } = useAppStore()
  const [config, setConfig] = useState<TransformerConfig | null>(null)

  useEffect(() => {
    if (!selectedTransformerId) { setConfig(null); return }
    api.getTransformer(selectedTransformerId)
      .then(setConfig)
      .catch(() => setConfig(null))
  }, [selectedTransformerId])

  return (
    <div className="flex gap-4 h-full min-h-0">
      {/* left sidebar */}
      <div className="w-72 shrink-0 space-y-4 overflow-y-auto">
        <ControlPanel />
        <RelayGrid />
      </div>

      {/* center — canvas */}
      <div className="flex-1 min-w-0 bg-panel border border-border rounded-lg overflow-hidden flex items-center justify-center">
        <TransformerCanvas config={config} />
      </div>

      {/* right sidebar */}
      <div className="w-80 shrink-0 overflow-y-auto">
        <StatusPanel />
      </div>
    </div>
  )
}

function LogsTab() {
  const [logs, setLogs]     = useState<{ name: string; size: number; modified: number }[]>([])
  const [content, setContent] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    api.getLogs().then((r) => setLogs(r.files)).catch(() => {})
  }, [])

  const openLog = async (name: string) => {
    setSelected(name)
    const r = await api.getLog(name).catch(() => null)
    setContent(r?.content ?? null)
  }

  return (
    <div className="flex gap-4 h-full min-h-0">
      <div className="w-72 shrink-0 bg-panel border border-border rounded-lg overflow-y-auto p-2 space-y-1">
        <div className="text-xs font-mono text-muted uppercase tracking-widest px-2 py-1">Log Files</div>
        {logs.length === 0 && (
          <div className="text-xs text-muted font-mono px-2 py-4 text-center">No logs yet</div>
        )}
        {logs.map((f) => (
          <button
            key={f.name}
            onClick={() => openLog(f.name)}
            className={`w-full text-left px-2 py-2 rounded text-xs font-mono transition-colors
              ${selected === f.name ? 'bg-primary/20 text-white' : 'text-muted hover:text-white hover:bg-card'}`}
          >
            <div className="text-white text-xs truncate">{f.name}</div>
            <div className="text-muted text-xs">{(f.size / 1024).toFixed(1)} KB</div>
          </button>
        ))}
      </div>
      <div className="flex-1 bg-panel border border-border rounded-lg overflow-auto p-4">
        {content == null ? (
          <div className="text-muted text-sm font-mono text-center py-12">
            Select a log file to view
          </div>
        ) : (
          <pre className="text-xs font-mono text-green-300 whitespace-pre-wrap">{content}</pre>
        )}
      </div>
    </div>
  )
}

export function AppShell() {
  const [tab, setTab] = useState<Tab>('dashboard')
  useWebSocket()

  return (
    <div className="h-screen bg-surface flex flex-col text-white overflow-hidden">
      <Header tab={tab} setTab={setTab} />
      <main className="flex-1 min-h-0 p-4">
        {tab === 'dashboard' && <DashboardTab />}
        {tab === 'editor'    && (
          <div className="max-w-2xl mx-auto">
            <TransformerEditor />
          </div>
        )}
        {tab === 'logs'      && <LogsTab />}
      </main>
    </div>
  )
}
