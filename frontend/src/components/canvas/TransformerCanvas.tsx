/**
 * TransformerCanvas — industrial animated transformer diagram (Konva/react-konva).
 *
 * Draws primary windings on the left, iron core in the center, secondary
 * windings on the right.  Responds live to WebSocket animation state.
 */
import { useEffect, useRef, useState } from 'react'
import { Stage, Layer, Rect, Circle, Line, Text, Group } from 'react-konva'
import type Konva from 'konva'
import type { TransformerConfig, WindingConfig, TapConfig } from '@/types'
import { useAppStore } from '@/store/useAppStore'

// ── layout constants ─────────────────────────────────────────────────────────
const STAGE_W = 760
const STAGE_H = 520
const CORE_X  = STAGE_W / 2 - 15
const CORE_W  = 30
const CORE_H  = 380
const CORE_Y  = (STAGE_H - CORE_H) / 2

const COIL_AMP    = 22    // sine wave amplitude
const COIL_SEG    = 6     // turns per winding
const PIN_R       = 9
const TAP_R       = 7
const LABEL_PAD   = 14
const COL_LEFT_X  = CORE_X - 140
const COL_RIGHT_X = CORE_X + CORE_W + 140

// ── color palette ─────────────────────────────────────────────────────────────
const C = {
  bg:           '#0d1117',
  core:         '#2a3448',
  coreStroke:   '#3d4f68',
  coilIdle:     '#3a6aaa',
  coilActive:   '#00ff88',
  coilPass:     '#22c55e',
  coilFail:     '#ef4444',
  pinIdle:      '#4a80c4',
  pinActive:    '#00ff88',
  pinFail:      '#ef4444',
  tapIdle:      '#2a5a9a',
  tapActive:    '#00e5ff',
  labelText:    '#94a3b8',
  activeText:   '#00ff88',
  passText:     '#22c55e',
  failText:     '#ef4444',
  lead:         '#2a3a58',
  leadActive:   '#00ff8860',
  grid:         '#161b22',
}

// ── helpers ───────────────────────────────────────────────────────────────────

function coilPoints(
  cx: number, y1: number, y2: number,
  side: 'left' | 'right',
  turns = COIL_SEG,
): number[] {
  const dir = side === 'left' ? -1 : 1
  const pts: number[] = []
  const steps = turns * 32
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    const y = y1 + t * (y2 - y1)
    const x = cx + dir * COIL_AMP * Math.sin(t * turns * Math.PI * 2)
    pts.push(x, y)
  }
  return pts
}

function windingColor(
  winding: WindingConfig,
  activePrimary: string | null,
  activeSecondary: string | null,
  appState: string,
): string {
  const id = winding.id
  const isActive = id === activePrimary || id === activeSecondary
  if (appState === 'PASS' && isActive) return C.coilPass
  if (appState === 'FAIL' && isActive) return C.coilFail
  if (isActive) return C.coilActive
  return C.coilIdle
}

function pinColor(
  winding: WindingConfig,
  which: 'start' | 'end',
  activePrimary: string | null,
  activeSecondary: string | null,
  appState: string,
  relayStates: Record<string, boolean>,
): string {
  const relay = which === 'start' ? winding.relay_a : winding.relay_b
  const relayOn = relay != null && relayStates[String(relay)]
  if (relayOn) {
    if (appState === 'FAIL') return C.pinFail
    return C.pinActive
  }
  const id = winding.id
  if (id === activePrimary || id === activeSecondary) return C.pinActive
  return C.pinIdle
}

// ── particle animation hook ───────────────────────────────────────────────────

interface Particle { x: number; y: number; progress: number; speed: number }

function useParticles(
  active: boolean,
  path: { x1: number; y1: number; x2: number; y2: number } | null,
): Particle[] {
  const [particles, setParticles] = useState<Particle[]>([])
  const rafRef = useRef<number>(0)

  useEffect(() => {
    if (!active || !path) {
      setParticles([])
      return
    }
    const initParticles: Particle[] = Array.from({ length: 5 }, (_, i) => ({
      x: 0, y: 0,
      progress: i / 5,
      speed: 0.008 + Math.random() * 0.004,
    }))
    setParticles(initParticles)

    const animate = () => {
      setParticles((prev) =>
        prev.map((p) => {
          const t = (p.progress + p.speed) % 1
          return {
            ...p,
            progress: t,
            x: path.x1 + (path.x2 - path.x1) * t,
            y: path.y1 + (path.y2 - path.y1) * t,
          }
        }),
      )
      rafRef.current = requestAnimationFrame(animate)
    }
    rafRef.current = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(rafRef.current)
  }, [active, path])

  return particles
}

// ── winding component ────────────────────────────────────────────────────────

interface WindingShapeProps {
  winding: WindingConfig
  side: 'left' | 'right'
  centerY: number
  height: number
  activePrimary: string | null
  activeSecondary: string | null
  appState: string
  relayStates: Record<string, boolean>
}

function WindingShape({
  winding, side, centerY, height,
  activePrimary, activeSecondary, appState, relayStates,
}: WindingShapeProps) {
  const cx = side === 'left' ? COL_LEFT_X : COL_RIGHT_X
  const y1 = centerY - height / 2
  const y2 = centerY + height / 2
  const coreX = side === 'left' ? CORE_X : CORE_X + CORE_W
  const color = windingColor(winding, activePrimary, activeSecondary, appState)
  const isActive = winding.id === activePrimary || winding.id === activeSecondary

  const pts = coilPoints(cx, y1, y2, side)

  // lead lines: coil center → core
  const leadY1 = y1
  const leadY2 = y2

  return (
    <Group>
      {/* lead lines */}
      <Line
        points={[cx, leadY1, coreX, leadY1]}
        stroke={isActive ? C.leadActive : C.lead}
        strokeWidth={isActive ? 2.5 : 1.5}
        dash={isActive ? [] : [6, 4]}
      />
      <Line
        points={[cx, leadY2, coreX, leadY2]}
        stroke={isActive ? C.leadActive : C.lead}
        strokeWidth={isActive ? 2.5 : 1.5}
        dash={isActive ? [] : [6, 4]}
      />

      {/* coil */}
      <Line
        points={pts}
        stroke={color}
        strokeWidth={isActive ? 3 : 2}
        tension={0}
        shadowColor={isActive ? color : undefined}
        shadowBlur={isActive ? 14 : 0}
        shadowOpacity={0.7}
      />

      {/* start pin */}
      <Circle
        x={cx}
        y={y1}
        radius={PIN_R}
        fill={pinColor(winding, 'start', activePrimary, activeSecondary, appState, relayStates)}
        stroke={isActive ? '#ffffff40' : '#00000060'}
        strokeWidth={1.5}
        shadowColor={isActive ? C.pinActive : undefined}
        shadowBlur={isActive ? 10 : 0}
      />

      {/* end pin */}
      <Circle
        x={cx}
        y={y2}
        radius={PIN_R}
        fill={pinColor(winding, 'end', activePrimary, activeSecondary, appState, relayStates)}
        stroke={isActive ? '#ffffff40' : '#00000060'}
        strokeWidth={1.5}
        shadowColor={isActive ? C.pinActive : undefined}
        shadowBlur={isActive ? 10 : 0}
      />

      {/* tap nodes */}
      {winding.taps.map((tap, i) => {
        const tapY = y1 + (y2 - y1) * ((i + 1) / (winding.taps.length + 1))
        const tapActive =
          (isActive) &&
          (tap.relay_a != null && relayStates[String(tap.relay_a)]) ||
          (tap.relay_b != null && relayStates[String(tap.relay_b)])
        return (
          <Group key={i}>
            <Line
              points={[cx, tapY, coreX, tapY]}
              stroke={tapActive ? '#00e5ff80' : '#1e2d4440'}
              strokeWidth={tapActive ? 2 : 1}
              dash={[4, 4]}
            />
            <Circle
              x={cx}
              y={tapY}
              radius={TAP_R}
              fill={tapActive ? C.tapActive : C.tapIdle}
              stroke={tapActive ? '#ffffff30' : '#00000050'}
              strokeWidth={1}
              shadowColor={tapActive ? C.tapActive : undefined}
              shadowBlur={tapActive ? 8 : 0}
            />
            <Text
              x={side === 'left' ? cx - 60 : cx + 14}
              y={tapY - 8}
              text={tap.label ?? `${tap.voltage}V`}
              fill={tapActive ? C.tapActive : C.labelText}
              fontSize={11}
              fontFamily="JetBrains Mono, monospace"
            />
          </Group>
        )
      })}

      {/* winding label */}
      <Text
        x={side === 'left' ? cx - LABEL_PAD - 64 : cx + LABEL_PAD}
        y={centerY - 9}
        text={`${winding.id}\n${winding.voltage}V`}
        fill={isActive ? C.activeText : C.labelText}
        fontSize={12}
        fontFamily="JetBrains Mono, monospace"
        fontStyle={isActive ? 'bold' : 'normal'}
        align={side === 'left' ? 'right' : 'left'}
        width={60}
      />

      {/* relay badge */}
      {winding.relay_a != null && (
        <Text
          x={side === 'left' ? cx - 36 : cx + 14}
          y={y1 - 18}
          text={`RL${winding.relay_a}`}
          fill={relayStates[String(winding.relay_a)] ? C.pinActive : C.labelText}
          fontSize={10}
          fontFamily="JetBrains Mono, monospace"
        />
      )}
      {winding.relay_b != null && (
        <Text
          x={side === 'left' ? cx - 36 : cx + 14}
          y={y2 + 6}
          text={`RL${winding.relay_b}`}
          fill={relayStates[String(winding.relay_b)] ? C.pinActive : C.labelText}
          fontSize={10}
          fontFamily="JetBrains Mono, monospace"
        />
      )}
    </Group>
  )
}

// ── main canvas ───────────────────────────────────────────────────────────────

interface Props {
  config: TransformerConfig | null
}

export function TransformerCanvas({ config }: Props) {
  const {
    appState, animActivePrimary, animActiveSecondary,
    relayStates, currentVoltage, expectedVoltage,
  } = useAppStore()

  const isActive = animActivePrimary != null || animActiveSecondary != null
  const particlePath = isActive ? {
    x1: COL_LEFT_X,
    y1: STAGE_H / 2,
    x2: COL_RIGHT_X,
    y2: STAGE_H / 2,
  } : null
  const particles = useParticles(isActive, particlePath)

  if (!config) {
    return (
      <div className="flex items-center justify-center h-full text-muted text-sm font-mono">
        Select a transformer to view diagram
      </div>
    )
  }

  const primaryCount   = config.primary.length
  const secondaryCount = config.secondary.length

  const assignLayout = (windings: WindingConfig[], count: number) => {
    const spacing = CORE_H / (count + 1)
    return windings.map((w, i) => ({
      winding: w,
      centerY: CORE_Y + spacing * (i + 1),
      height: Math.min(spacing * 0.75, 160),
    }))
  }

  const primaryLayout   = assignLayout(config.primary,   primaryCount)
  const secondaryLayout = assignLayout(config.secondary, secondaryCount)

  return (
    <Stage width={STAGE_W} height={STAGE_H}>
      <Layer>
        {/* background */}
        <Rect x={0} y={0} width={STAGE_W} height={STAGE_H} fill={C.bg} />

        {/* subtle grid */}
        {Array.from({ length: Math.floor(STAGE_W / 40) + 1 }, (_, i) => (
          <Line
            key={`gv${i}`}
            points={[i * 40, 0, i * 40, STAGE_H]}
            stroke={C.grid}
            strokeWidth={1}
          />
        ))}
        {Array.from({ length: Math.floor(STAGE_H / 40) + 1 }, (_, i) => (
          <Line
            key={`gh${i}`}
            points={[0, i * 40, STAGE_W, i * 40]}
            stroke={C.grid}
            strokeWidth={1}
          />
        ))}

        {/* iron core */}
        <Rect
          x={CORE_X}
          y={CORE_Y}
          width={CORE_W}
          height={CORE_H}
          fill={C.core}
          stroke={C.coreStroke}
          strokeWidth={2}
          cornerRadius={4}
        />
        {/* core lamination lines */}
        {Array.from({ length: 8 }, (_, i) => (
          <Line
            key={`lam${i}`}
            points={[
              CORE_X + 4, CORE_Y + (CORE_H / 9) * (i + 1),
              CORE_X + CORE_W - 4, CORE_Y + (CORE_H / 9) * (i + 1),
            ]}
            stroke={C.coreStroke}
            strokeWidth={1}
            opacity={0.6}
          />
        ))}
        <Text
          x={CORE_X}
          y={CORE_Y + CORE_H + 6}
          width={CORE_W}
          text="CORE"
          fill={C.labelText}
          fontSize={10}
          fontFamily="JetBrains Mono, monospace"
          align="center"
        />

        {/* primary label */}
        <Text
          x={COL_LEFT_X - 80}
          y={CORE_Y - 28}
          text="PRIMARY"
          fill={C.labelText}
          fontSize={11}
          fontFamily="JetBrains Mono, monospace"
          letterSpacing={2}
        />

        {/* secondary label */}
        <Text
          x={COL_RIGHT_X - 10}
          y={CORE_Y - 28}
          text="SECONDARY"
          fill={C.labelText}
          fontSize={11}
          fontFamily="JetBrains Mono, monospace"
          letterSpacing={2}
        />

        {/* primary windings */}
        {primaryLayout.map(({ winding, centerY, height }) => (
          <WindingShape
            key={winding.id}
            winding={winding}
            side="left"
            centerY={centerY}
            height={height}
            activePrimary={animActivePrimary}
            activeSecondary={animActiveSecondary}
            appState={appState}
            relayStates={relayStates}
          />
        ))}

        {/* secondary windings */}
        {secondaryLayout.map(({ winding, centerY, height }) => (
          <WindingShape
            key={winding.id}
            winding={winding}
            side="right"
            centerY={centerY}
            height={height}
            activePrimary={animActivePrimary}
            activeSecondary={animActiveSecondary}
            appState={appState}
            relayStates={relayStates}
          />
        ))}

        {/* measurement path overlay */}
        {isActive && (
          <Line
            points={[
              COL_LEFT_X, STAGE_H / 2,
              CORE_X, STAGE_H / 2,
              CORE_X + CORE_W, STAGE_H / 2,
              COL_RIGHT_X, STAGE_H / 2,
            ]}
            stroke="#00ff8830"
            strokeWidth={3}
            dash={[8, 6]}
          />
        )}

        {/* flow particles */}
        {particles.map((p, i) => (
          <Circle
            key={i}
            x={p.x}
            y={p.y}
            radius={4}
            fill={C.coilActive}
            opacity={0.8}
            shadowColor={C.coilActive}
            shadowBlur={8}
          />
        ))}

        {/* live voltage readout */}
        {currentVoltage != null && (
          <Group>
            <Rect
              x={STAGE_W / 2 - 80}
              y={STAGE_H - 48}
              width={160}
              height={34}
              fill="#161b22"
              stroke="#00ff8840"
              strokeWidth={1}
              cornerRadius={4}
            />
            <Text
              x={STAGE_W / 2 - 80}
              y={STAGE_H - 40}
              width={160}
              text={`${currentVoltage.toFixed(3)} V`}
              fill={C.coilActive}
              fontSize={16}
              fontFamily="JetBrains Mono, monospace"
              align="center"
              fontStyle="bold"
            />
          </Group>
        )}

        {/* PASS/FAIL overlay */}
        {(appState === 'PASS' || appState === 'FAIL') && (
          <Group>
            <Rect
              x={STAGE_W / 2 - 100}
              y={STAGE_H / 2 - 32}
              width={200}
              height={64}
              fill={appState === 'PASS' ? '#22c55e18' : '#ef444418'}
              stroke={appState === 'PASS' ? C.coilPass : C.coilFail}
              strokeWidth={2}
              cornerRadius={8}
            />
            <Text
              x={STAGE_W / 2 - 100}
              y={STAGE_H / 2 - 16}
              width={200}
              text={appState}
              fill={appState === 'PASS' ? C.coilPass : C.coilFail}
              fontSize={28}
              fontFamily="JetBrains Mono, monospace"
              fontStyle="bold"
              align="center"
            />
          </Group>
        )}
      </Layer>
    </Stage>
  )
}
