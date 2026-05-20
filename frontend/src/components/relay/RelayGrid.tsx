/**
 * RelayGrid — 34-relay live status board.
 * RL1-16  = Side A (voltmeter +), RL17-32 = Side B (voltmeter −),
 * RL33-34 = Gate relays.
 */
import { useAppStore } from '@/store/useAppStore'

interface RelayBtnProps {
  id: number
  on: boolean
  group: 'A' | 'B' | 'Gate'
}

function RelayBtn({ id, on, group }: RelayBtnProps) {
  const groupColors = {
    A:    { on: 'bg-glow text-black shadow-glow', off: 'bg-card text-muted border-border' },
    B:    { on: 'bg-accent text-black shadow-accent', off: 'bg-card text-muted border-border' },
    Gate: { on: 'bg-warning text-black', off: 'bg-card text-muted border-border' },
  }
  const colors = groupColors[group]
  return (
    <div
      className={`
        flex items-center justify-center
        w-12 h-9 rounded text-xs font-mono font-medium
        border transition-all duration-150
        ${on ? colors.on : colors.off}
        ${on ? 'scale-105' : ''}
      `}
    >
      {id}
    </div>
  )
}

export function RelayGrid() {
  const relayStates = useAppStore((s) => s.relayStates)

  const isOn = (id: number) => Boolean(relayStates[String(id)])

  const activeA    = Array.from({ length: 16 }, (_, i) => i + 1).filter(isOn)
  const activeB    = Array.from({ length: 16 }, (_, i) => i + 17).filter(isOn)
  const gateA      = isOn(33)
  const gateB      = isOn(34)
  const activeCount = activeA.length + activeB.length + (gateA ? 1 : 0) + (gateB ? 1 : 0)

  return (
    <div className="bg-panel rounded-lg border border-border p-4 space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-mono text-muted uppercase tracking-widest">Relay Matrix</span>
        <span className={`text-xs font-mono px-2 py-0.5 rounded ${activeCount > 0 ? 'text-glow bg-glow/10' : 'text-muted'}`}>
          {activeCount} active
        </span>
      </div>

      {/* Side A — RL1-16 */}
      <div>
        <div className="text-xs text-muted font-mono mb-2">
          Side A — Voltmeter <span className="text-accent">+</span>
        </div>
        <div className="grid grid-cols-8 gap-1">
          {Array.from({ length: 16 }, (_, i) => i + 1).map((id) => (
            <RelayBtn key={id} id={id} on={isOn(id)} group="A" />
          ))}
        </div>
      </div>

      {/* Side B — RL17-32 */}
      <div>
        <div className="text-xs text-muted font-mono mb-2">
          Side B — Voltmeter <span className="text-warning">−</span>
        </div>
        <div className="grid grid-cols-8 gap-1">
          {Array.from({ length: 16 }, (_, i) => i + 17).map((id) => (
            <RelayBtn key={id} id={id} on={isOn(id)} group="B" />
          ))}
        </div>
      </div>

      {/* Gate relays */}
      <div>
        <div className="text-xs text-muted font-mono mb-2">Gate Relays</div>
        <div className="flex gap-2">
          <RelayBtn id={33} on={gateA} group="Gate" />
          <RelayBtn id={34} on={gateB} group="Gate" />
        </div>
      </div>
    </div>
  )
}
