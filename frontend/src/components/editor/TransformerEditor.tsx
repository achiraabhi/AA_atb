/**
 * TransformerEditor — browser-based visual transformer topology editor.
 * Allows creating / editing transformer configs that map to the same JSON
 * format the Python backend consumes.
 */
import { useState, useCallback } from 'react'
import type { TransformerConfig, WindingConfig, TapConfig, AutoMatrixConfig } from '@/types'
import * as api from '@/api/client'

const emptyWinding = (id: string, side: 'primary' | 'secondary', index: number): WindingConfig => ({
  id,
  winding_type: 'basic_winding',
  start_pin: side === 'primary' ? index * 2 + 1 : index * 2 + 100,
  end_pin:   side === 'primary' ? index * 2 + 2 : index * 2 + 101,
  voltage:   side === 'primary' ? 230 : 115,
  dot_polarity: true,
  relay_a: null,
  relay_b: null,
  meas_channel: -1,
  taps: [],
})

const emptyConfig = (): TransformerConfig => ({
  name: 'New Transformer',
  transformer_id: 'new_transformer',
  type: 'unknown',
  rated_power_va: 0,
  rated_frequency_hz: 50,
  notes: '',
  primary: [emptyWinding('P1', 'primary', 0)],
  secondary: [emptyWinding('S1', 'secondary', 0)],
  tests: [],
  auto_matrix: { enabled: true, energize_winding: 'P1' },
})

// ── relay picker popup ────────────────────────────────────────────────────────

function RelayPicker({
  group, value, onChange, onClose,
}: {
  group: 'A' | 'B'
  value: number | null | undefined
  onChange: (v: number | null) => void
  onClose: () => void
}) {
  const range = group === 'A' ? [1, 16] : [17, 32]
  return (
    <div className="absolute z-50 bg-card border border-border rounded-lg p-3 shadow-xl w-52">
      <div className="text-xs font-mono text-muted mb-2 flex justify-between">
        <span>RL{range[0]}–RL{range[1]}</span>
        <button onClick={onClose} className="text-muted hover:text-white">✕</button>
      </div>
      <div className="grid grid-cols-4 gap-1 mb-2">
        {Array.from({ length: range[1] - range[0] + 1 }, (_, i) => i + range[0]).map((rl) => (
          <button
            key={rl}
            onClick={() => { onChange(rl); onClose() }}
            className={`py-1 rounded text-xs font-mono font-medium transition-colors
              ${value === rl
                ? 'bg-glow text-black'
                : 'bg-surface text-muted hover:bg-muted hover:text-white'}`}
          >
            {rl}
          </button>
        ))}
      </div>
      <button
        onClick={() => { onChange(null); onClose() }}
        className="w-full py-1 text-xs font-mono text-muted hover:text-danger border border-border rounded"
      >
        Clear
      </button>
    </div>
  )
}

// ── tap row editor ────────────────────────────────────────────────────────────

function TapRow({
  tap, onChange, onDelete,
}: {
  tap: TapConfig
  onChange: (t: TapConfig) => void
  onDelete: () => void
}) {
  const [showRA, setShowRA] = useState(false)
  const [showRB, setShowRB] = useState(false)

  return (
    <div className="bg-surface border border-border/50 rounded p-2 grid grid-cols-5 gap-1 items-center text-xs font-mono relative">
      <input
        className="bg-card border border-border rounded px-2 py-1 text-white w-full"
        placeholder="Pin"
        value={tap.pin}
        onChange={(e) => onChange({ ...tap, pin: Number(e.target.value) })}
        type="number"
      />
      <input
        className="bg-card border border-border rounded px-2 py-1 text-white w-full"
        placeholder="V"
        value={tap.voltage}
        onChange={(e) => onChange({ ...tap, voltage: Number(e.target.value) })}
        type="number"
      />
      <input
        className="bg-card border border-border rounded px-2 py-1 text-white w-full col-span-1"
        placeholder="Label"
        value={tap.label ?? ''}
        onChange={(e) => onChange({ ...tap, label: e.target.value })}
      />

      {/* relay_a */}
      <div className="relative">
        <button
          onClick={() => setShowRA(!showRA)}
          className={`w-full py-1 rounded border text-xs font-mono
            ${tap.relay_a != null ? 'border-glow text-glow' : 'border-border text-muted'}`}
        >
          {tap.relay_a != null ? `RL${tap.relay_a}` : 'A—'}
        </button>
        {showRA && (
          <RelayPicker
            group="A"
            value={tap.relay_a}
            onChange={(v) => onChange({ ...tap, relay_a: v })}
            onClose={() => setShowRA(false)}
          />
        )}
      </div>

      {/* delete */}
      <button onClick={onDelete} className="text-muted hover:text-danger">✕</button>

      {/* relay_b in second row */}
      <div className="col-span-5 flex gap-1">
        <div className="relative">
          <button
            onClick={() => setShowRB(!showRB)}
            className={`py-1 px-3 rounded border text-xs font-mono
              ${tap.relay_b != null ? 'border-accent text-accent' : 'border-border text-muted'}`}
          >
            {tap.relay_b != null ? `RL${tap.relay_b}` : 'B—'}
          </button>
          {showRB && (
            <RelayPicker
              group="B"
              value={tap.relay_b}
              onChange={(v) => onChange({ ...tap, relay_b: v })}
              onClose={() => setShowRB(false)}
            />
          )}
        </div>
        <span className="text-muted self-center text-xs">tap relay B (start→tap direction)</span>
      </div>
    </div>
  )
}

// ── winding card ──────────────────────────────────────────────────────────────

function WindingCard({
  winding, onChange, onDelete, side,
}: {
  winding: WindingConfig
  onChange: (w: WindingConfig) => void
  onDelete: () => void
  side: 'primary' | 'secondary'
}) {
  const [collapsed, setCollapsed] = useState(false)
  const [showRA, setShowRA] = useState(false)
  const [showRB, setShowRB] = useState(false)

  const accentClass = side === 'primary' ? 'border-primary/50' : 'border-accent/50'

  return (
    <div className={`bg-card border ${accentClass} rounded-lg p-3 space-y-2`}>
      {/* header */}
      <div className="flex items-center gap-2">
        <button onClick={() => setCollapsed(!collapsed)} className="text-muted hover:text-white text-xs">
          {collapsed ? '▶' : '▼'}
        </button>
        <input
          className="bg-surface border border-border rounded px-2 py-0.5 text-sm font-mono text-white w-20"
          value={winding.id}
          onChange={(e) => onChange({ ...winding, id: e.target.value })}
        />
        <span className="text-muted text-xs">|</span>
        <input
          className="bg-surface border border-border rounded px-2 py-0.5 text-sm font-mono text-white w-24"
          placeholder="Voltage"
          value={winding.voltage}
          type="number"
          onChange={(e) => onChange({ ...winding, voltage: Number(e.target.value) })}
        />
        <span className="text-muted text-xs">V</span>
        <button onClick={onDelete} className="ml-auto text-muted hover:text-danger text-xs">✕</button>
      </div>

      {!collapsed && (
        <>
          {/* pins */}
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            <div className="space-y-1">
              <label className="text-muted">Start Pin</label>
              <input
                className="bg-surface border border-border rounded px-2 py-1 text-white w-full"
                type="number"
                value={winding.start_pin}
                onChange={(e) => onChange({ ...winding, start_pin: Number(e.target.value) })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-muted">End Pin</label>
              <input
                className="bg-surface border border-border rounded px-2 py-1 text-white w-full"
                type="number"
                value={winding.end_pin}
                onChange={(e) => onChange({ ...winding, end_pin: Number(e.target.value) })}
              />
            </div>
          </div>

          {/* relay assignment */}
          <div className="flex gap-2 items-center">
            <span className="text-xs text-muted font-mono w-16">Relay A (+)</span>
            <div className="relative">
              <button
                onClick={() => setShowRA(!showRA)}
                className={`py-1 px-3 rounded border text-xs font-mono
                  ${winding.relay_a != null ? 'border-glow text-glow' : 'border-border text-muted'}`}
              >
                {winding.relay_a != null ? `RL${winding.relay_a}` : 'None'}
              </button>
              {showRA && (
                <RelayPicker
                  group="A"
                  value={winding.relay_a}
                  onChange={(v) => onChange({ ...winding, relay_a: v })}
                  onClose={() => setShowRA(false)}
                />
              )}
            </div>
            <span className="text-xs text-muted font-mono">Relay B (−)</span>
            <div className="relative">
              <button
                onClick={() => setShowRB(!showRB)}
                className={`py-1 px-3 rounded border text-xs font-mono
                  ${winding.relay_b != null ? 'border-accent text-accent' : 'border-border text-muted'}`}
              >
                {winding.relay_b != null ? `RL${winding.relay_b}` : 'None'}
              </button>
              {showRB && (
                <RelayPicker
                  group="B"
                  value={winding.relay_b}
                  onChange={(v) => onChange({ ...winding, relay_b: v })}
                  onClose={() => setShowRB(false)}
                />
              )}
            </div>
          </div>

          {/* taps */}
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs font-mono text-muted">
              <span>Taps ({winding.taps.length})</span>
              <button
                onClick={() => onChange({
                  ...winding,
                  taps: [...winding.taps, { pin: 0, voltage: 0, label: '', relay_a: null, relay_b: null }],
                })}
                className="text-accent hover:text-white px-2"
              >
                + Add Tap
              </button>
            </div>
            {winding.taps.map((tap, i) => (
              <TapRow
                key={i}
                tap={tap}
                onChange={(t) => onChange({
                  ...winding,
                  taps: winding.taps.map((x, j) => j === i ? t : x),
                })}
                onDelete={() => onChange({
                  ...winding,
                  taps: winding.taps.filter((_, j) => j !== i),
                })}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ── main editor ────────────────────────────────────────────────────────────────

interface Props {
  initialConfig?: TransformerConfig
  onSaved?: (cfg: TransformerConfig) => void
}

export function TransformerEditor({ initialConfig, onSaved }: Props) {
  const [config, setConfig] = useState<TransformerConfig>(initialConfig ?? emptyConfig())
  const [saving, setSaving] = useState(false)
  const [saved, setSaved]   = useState(false)
  const [error, setError]   = useState<string | null>(null)

  const updatePrimary = useCallback((idx: number, w: WindingConfig) => {
    setConfig((c) => ({ ...c, primary: c.primary.map((x, i) => i === idx ? w : x) }))
  }, [])

  const updateSecondary = useCallback((idx: number, w: WindingConfig) => {
    setConfig((c) => ({ ...c, secondary: c.secondary.map((x, i) => i === idx ? w : x) }))
  }, [])

  const addPrimary = () => {
    const id = `P${config.primary.length + 1}`
    setConfig((c) => ({ ...c, primary: [...c.primary, emptyWinding(id, 'primary', c.primary.length)] }))
  }

  const addSecondary = () => {
    const id = `S${config.secondary.length + 1}`
    setConfig((c) => ({ ...c, secondary: [...c.secondary, emptyWinding(id, 'secondary', c.secondary.length)] }))
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      // auto-fill tests if using auto_matrix
      const toSave = {
        ...config,
        tests: config.tests.length > 0 ? config.tests : [],
      }
      await api.saveTransformer(toSave)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
      onSaved?.(toSave)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const autoMatrix = config.auto_matrix

  return (
    <div className="bg-panel border border-border rounded-lg space-y-4 overflow-auto max-h-[calc(100vh-120px)]">
      {/* toolbar */}
      <div className="flex items-center gap-3 px-4 pt-4 pb-2 border-b border-border sticky top-0 bg-panel z-10">
        <span className="text-xs font-mono text-muted uppercase tracking-widest">Transformer Editor</span>
        <div className="flex-1" />
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-4 py-1.5 rounded font-mono text-sm font-medium
                     bg-glow text-black hover:bg-green-400 disabled:opacity-50 transition-colors"
        >
          {saving ? 'Saving…' : saved ? '✓ Saved' : '💾 Save'}
        </button>
      </div>

      <div className="px-4 space-y-4 pb-6">
        {error && (
          <div className="bg-danger/10 border border-danger/30 rounded px-3 py-2 text-sm text-danger font-mono">
            {error}
          </div>
        )}

        {/* metadata */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-mono text-muted">Name</label>
            <input
              className="w-full bg-card border border-border rounded px-3 py-2 text-sm font-mono text-white"
              value={config.name}
              onChange={(e) => setConfig({ ...config, name: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-mono text-muted">ID (filename)</label>
            <input
              className="w-full bg-card border border-border rounded px-3 py-2 text-sm font-mono text-white"
              value={config.transformer_id}
              onChange={(e) => setConfig({ ...config, transformer_id: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-mono text-muted">Power (VA)</label>
            <input
              type="number"
              className="w-full bg-card border border-border rounded px-3 py-2 text-sm font-mono text-white"
              value={config.rated_power_va ?? 0}
              onChange={(e) => setConfig({ ...config, rated_power_va: Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-mono text-muted">Frequency (Hz)</label>
            <input
              type="number"
              className="w-full bg-card border border-border rounded px-3 py-2 text-sm font-mono text-white"
              value={config.rated_frequency_hz ?? 50}
              onChange={(e) => setConfig({ ...config, rated_frequency_hz: Number(e.target.value) })}
            />
          </div>
        </div>

        {/* auto-matrix toggle */}
        <div className="bg-card border border-border rounded-lg px-4 py-3 flex items-center gap-3">
          <input
            type="checkbox"
            id="auto_matrix"
            checked={autoMatrix?.enabled ?? false}
            onChange={(e) => setConfig({
              ...config,
              auto_matrix: { ...(autoMatrix ?? { energize_winding: '' }), enabled: e.target.checked },
            })}
            className="accent-glow"
          />
          <label htmlFor="auto_matrix" className="text-sm font-mono text-white cursor-pointer">
            Auto-Matrix test generation
          </label>
          {autoMatrix?.enabled && (
            <input
              className="ml-auto bg-surface border border-border rounded px-2 py-1 text-xs font-mono text-white w-24"
              placeholder="Energize"
              value={autoMatrix.energize_winding}
              onChange={(e) => setConfig({
                ...config,
                auto_matrix: { ...autoMatrix, energize_winding: e.target.value },
              })}
            />
          )}
        </div>

        {/* primaries */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted">Primary Windings</h3>
            <button onClick={addPrimary} className="text-xs font-mono text-primary hover:text-white px-2 py-1 rounded border border-border hover:border-primary">
              + Add Primary
            </button>
          </div>
          {config.primary.map((w, i) => (
            <WindingCard
              key={i}
              winding={w}
              side="primary"
              onChange={(nw) => updatePrimary(i, nw)}
              onDelete={() => setConfig((c) => ({ ...c, primary: c.primary.filter((_, j) => j !== i) }))}
            />
          ))}
        </div>

        {/* secondaries */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted">Secondary Windings</h3>
            <button onClick={addSecondary} className="text-xs font-mono text-accent hover:text-white px-2 py-1 rounded border border-border hover:border-accent">
              + Add Secondary
            </button>
          </div>
          {config.secondary.map((w, i) => (
            <WindingCard
              key={i}
              winding={w}
              side="secondary"
              onChange={(nw) => updateSecondary(i, nw)}
              onDelete={() => setConfig((c) => ({ ...c, secondary: c.secondary.filter((_, j) => j !== i) }))}
            />
          ))}
        </div>

        {/* notes */}
        <div className="space-y-1">
          <label className="text-xs font-mono text-muted">Notes</label>
          <textarea
            className="w-full bg-card border border-border rounded px-3 py-2 text-sm font-mono text-white h-20 resize-none"
            value={config.notes ?? ''}
            onChange={(e) => setConfig({ ...config, notes: e.target.value })}
            placeholder="Optional notes…"
          />
        </div>
      </div>
    </div>
  )
}
