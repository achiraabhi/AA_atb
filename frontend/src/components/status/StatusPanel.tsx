/**
 * StatusPanel — live measurement cards + step results history.
 */
import { useAppStore } from '@/store/useAppStore'

function MeasCard({ label, value, unit, highlight = false, color = 'text-white' }: {
  label: string; value: string; unit?: string; highlight?: boolean; color?: string
}) {
  return (
    <div className={`
      bg-card border rounded-lg p-3 space-y-1
      ${highlight ? 'border-glow/50 shadow-glow/20' : 'border-border'}
    `}>
      <div className="text-xs font-mono text-muted uppercase tracking-wider">{label}</div>
      <div className={`text-2xl font-mono font-bold ${color}`}>
        {value}
        {unit && <span className="text-sm text-muted ml-1">{unit}</span>}
      </div>
    </div>
  )
}

function PassFailBadge({ passed }: { passed: boolean }) {
  return (
    <span className={`
      text-xs font-mono px-2 py-0.5 rounded font-semibold
      ${passed ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'}
    `}>
      {passed ? 'PASS' : 'FAIL'}
    </span>
  )
}

export function StatusPanel() {
  const {
    currentVoltage, expectedVoltage, tolerancePct,
    activePrimary, activeSecondary,
    stepResults, session, appState,
    progressPct, currentStepIndex, totalSteps,
  } = useAppStore()

  const deviation = (currentVoltage != null && expectedVoltage != null && expectedVoltage !== 0)
    ? Math.abs(currentVoltage - expectedVoltage) / expectedVoltage * 100
    : null

  const withinTol = deviation != null ? deviation <= tolerancePct : null

  const voltageColor =
    withinTol === null ? 'text-white'
    : withinTol ? 'text-glow'
    : 'text-danger'

  return (
    <div className="space-y-4">
      {/* live measurement cards */}
      <div className="grid grid-cols-2 gap-3">
        <MeasCard
          label="Measured Voltage"
          value={currentVoltage != null ? currentVoltage.toFixed(3) : '—'}
          unit="V"
          highlight={currentVoltage != null}
          color={voltageColor}
        />
        <MeasCard
          label="Expected Voltage"
          value={expectedVoltage != null ? expectedVoltage.toFixed(3) : '—'}
          unit="V"
          color="text-accent"
        />
        <MeasCard
          label="Deviation"
          value={deviation != null ? `${deviation.toFixed(2)}` : '—'}
          unit="%"
          color={withinTol === false ? 'text-danger' : withinTol ? 'text-success' : 'text-muted'}
        />
        <MeasCard
          label="Tolerance"
          value={`±${tolerancePct.toFixed(1)}`}
          unit="%"
          color="text-muted"
        />
      </div>

      {/* active path */}
      {(activePrimary || activeSecondary) && (
        <div className="bg-card border border-glow/30 rounded-lg px-4 py-3 flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-glow animate-pulse-fast" />
          <span className="font-mono text-sm">
            <span className="text-glow">{activePrimary ?? '—'}</span>
            <span className="text-muted mx-2">→</span>
            <span className="text-accent">{activeSecondary ?? '—'}</span>
          </span>
          {appState === 'TESTING' && (
            <span className="ml-auto text-xs text-muted font-mono">measuring…</span>
          )}
        </div>
      )}

      {/* session summary */}
      {session && (
        <div className="bg-card border border-border rounded-lg px-4 py-3 space-y-1">
          <div className="flex justify-between text-xs font-mono text-muted">
            <span>Operator: <span className="text-white">{session.operator || '—'}</span></span>
            <span>Transformer: <span className="text-white">{session.transformer_id}</span></span>
          </div>
          <div className="flex justify-between text-xs font-mono text-muted">
            <span>
              Passed: <span className="text-success">{session.passed_steps}</span>
              {' / '}
              <span className="text-white">{session.total_steps}</span>
            </span>
            {session.overall_pass != null && (
              <PassFailBadge passed={session.overall_pass} />
            )}
          </div>
        </div>
      )}

      {/* results table */}
      {stepResults.length > 0 && (
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-2 border-b border-border text-xs font-mono text-muted uppercase tracking-wider">
            Step Results
          </div>
          <div className="overflow-auto max-h-64">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="text-muted border-b border-border/50">
                  <th className="px-3 py-2 text-left">#</th>
                  <th className="px-3 py-2 text-left">Path</th>
                  <th className="px-3 py-2 text-right">Measured</th>
                  <th className="px-3 py-2 text-right">Expected</th>
                  <th className="px-3 py-2 text-center">Result</th>
                </tr>
              </thead>
              <tbody>
                {[...stepResults].reverse().map((r, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-surface/50">
                    <td className="px-3 py-2 text-muted">{r.step_index + 1}</td>
                    <td className="px-3 py-2 text-white">
                      {r.from_winding}<span className="text-muted mx-1">→</span>{r.to_winding}
                    </td>
                    <td className={`px-3 py-2 text-right ${r.passed ? 'text-glow' : 'text-danger'}`}>
                      {r.measured_voltage.toFixed(3)}V
                    </td>
                    <td className="px-3 py-2 text-right text-muted">
                      {r.expected_voltage.toFixed(3)}V
                    </td>
                    <td className="px-3 py-2 text-center">
                      <PassFailBadge passed={r.passed} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
