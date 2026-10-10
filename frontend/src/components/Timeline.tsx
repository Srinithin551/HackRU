/**
 * The replay "money shot": two stacked charts sharing one time axis (syncId keeps
 * their crosshairs together). Never one chart with two y-axes:
 *   top:    heart rate (bpm) + dashed baseline
 *   bottom: stress (0-100) colored by zone, dashed 40/70 thresholds, coach-flag dots
 * Every question is a shaded band across both (Q1, Q2...); the selected one is
 * highlighted. Clicking anywhere in a band selects that question.
 */
import { useId } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { color, STRESS_HIGH, STRESS_META, STRESS_RISING, stressColor, stressGradientStops } from '../styles/tokens.ts'
import type { CoachLevel, QuestionFeedback, QuestionKind, QuestionStats } from '../types.ts'

export interface QuestionView {
  id: string
  kind: QuestionKind
  label: string
  text: string
  transcript: string | null
  /** Answer window, epoch ms (null if the answer never started/ended). */
  start: number | null
  end: number | null
  stats: QuestionStats | null
  feedback: QuestionFeedback | null
  peak: number | null
}

export interface FlagView {
  t: number
  level: CoachLevel
  y: number | null
}

export interface ReportView {
  points: { t: number; hr: number | null; stress: number | null }[]
  start: number
  end: number
  baselineHr: number | null
  questions: QuestionView[]
  weakestId: string | null
  flags: FlagView[]
}

const MONO = 'Geist Mono Variable, ui-monospace, monospace'
const TICK = { fill: color.chartAxis, fontSize: 11, fontFamily: MONO }
const isNum = (v: number | null): v is number => typeof v === 'number'

/** mm:ss since the session started. */
const clock = (ms: number) => {
  const s = Math.max(0, Math.round(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

interface TooltipProps {
  active?: boolean
  label?: unknown
  payload?: readonly { dataKey?: unknown; value?: unknown }[]
}

export function Timeline({ view, selectedId, onSelect }: { view: ReportView; selectedId: string | null; onSelect: (id: string) => void }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const { points, start, end, questions, flags, baselineHr } = view
  const bands = questions.filter((q): q is QuestionView & { start: number; end: number } => q.start !== null && q.end !== null)
  const stressValues = points.map((p) => p.stress).filter(isNum)
  const max = stressValues.length ? Math.max(...stressValues) : 0
  const min = stressValues.length ? Math.min(...stressValues) : 0

  /** Chart click → the question whose answer window contains that moment. */
  const pick = (state: unknown) => {
    const x = Number((state as { activeLabel?: unknown } | null)?.activeLabel)
    if (!Number.isFinite(x)) return
    const hit = bands.find((q) => x >= q.start && x <= q.end)
    if (hit) onSelect(hit.id)
  }

  const renderTooltip = ({ active, label, payload }: TooltipProps) => {
    if (!active || !payload?.length) return null
    return (
      <div className="rounded-control border border-border-strong bg-surface-2 px-3 py-2 shadow-lg">
        <p className="font-mono text-micro text-ink-muted">{clock(Number(label) - start)}</p>
        {payload.map((p) =>
          typeof p.value === 'number' ? (
            <p key={String(p.dataKey)} className="text-caption text-ink">
              {p.dataKey === 'hr' ? `Heart rate ${Math.round(p.value)} bpm` : `Stress ${Math.round(p.value)}`}
            </p>
          ) : null,
        )}
      </div>
    )
  }

  const bandsFor = (withLabels: boolean) =>
    bands.map((q) => {
      const selected = q.id === selectedId
      return (
        <ReferenceArea
          key={q.id}
          x1={q.start}
          x2={q.end}
          fill={selected ? color.accent : color.ink}
          fillOpacity={selected ? 0.14 : 0.045}
          stroke="none"
          ifOverflow="hidden"
          label={
            withLabels
              ? { value: q.label === 'Warm-up' ? 'W' : q.label, position: 'insideTop', fill: selected ? color.ink : color.inkMuted, fontSize: 11, fontFamily: MONO }
              : undefined
          }
        />
      )
    })

  const xAxis = (visible: boolean) => (
    <XAxis
      dataKey="t"
      type="number"
      domain={[start, end]}
      hide={!visible}
      tick={TICK}
      tickLine={false}
      axisLine={{ stroke: color.hairline }}
      tickFormatter={(t: number) => clock(t - start)}
      minTickGap={32}
    />
  )

  return (
    <div className="mt-3 flex min-h-0 flex-1 cursor-pointer flex-col">
      <div className="min-h-0 flex-[3]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} syncId="replay" margin={{ top: 18, right: 12, bottom: 0, left: 0 }} onClick={pick}>
            <CartesianGrid stroke={color.chartGrid} vertical={false} />
            {xAxis(false)}
            <YAxis
              width={40}
              domain={['dataMin - 5', 'dataMax + 5']}
              tick={TICK}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v: number) => String(Math.round(v))}
            />
            {bandsFor(true)}
            {baselineHr !== null && (
              <ReferenceLine
                y={baselineHr}
                stroke={color.inkMuted}
                strokeDasharray="5 4"
                ifOverflow="extendDomain"
                label={{ value: `baseline ${Math.round(baselineHr)}`, position: 'insideBottomRight', fill: color.inkMuted, fontSize: 11, fontFamily: MONO }}
              />
            )}
            <Tooltip content={renderTooltip} cursor={{ stroke: color.borderStrong }} />
            <Line dataKey="hr" type="monotone" stroke={color.seriesHr} strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="min-h-0 flex-[2]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} syncId="replay" margin={{ top: 6, right: 12, bottom: 0, left: 0 }} onClick={pick}>
            <defs>
              <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="0" y2="1">
                {stressGradientStops(max, 0).map((s, i) => (
                  <stop key={i} offset={s.offset} stopColor={s.color} stopOpacity={0.18} />
                ))}
              </linearGradient>
              <linearGradient id={`${id}-line`} x1="0" y1="0" x2="0" y2="1">
                {stressGradientStops(max, min).map((s, i) => (
                  <stop key={i} offset={s.offset} stopColor={s.color} />
                ))}
              </linearGradient>
            </defs>
            <CartesianGrid stroke={color.chartGrid} vertical={false} />
            {xAxis(true)}
            <YAxis width={40} domain={[0, 100]} ticks={[0, STRESS_RISING, STRESS_HIGH, 100]} tick={TICK} tickLine={false} axisLine={false} />
            {bandsFor(false)}
            <ReferenceLine y={STRESS_RISING} stroke={color.rising} strokeOpacity={0.5} strokeDasharray="4 4" />
            <ReferenceLine y={STRESS_HIGH} stroke={color.high} strokeOpacity={0.5} strokeDasharray="4 4" />
            <Tooltip content={renderTooltip} cursor={{ stroke: color.borderStrong }} />
            <Area
              dataKey="stress"
              type="monotone"
              baseValue={0}
              stroke={max - min < 0.5 ? stressColor(max) : `url(#${id}-line)`}
              strokeWidth={2}
              fill={`url(#${id}-fill)`}
              isAnimationActive={false}
            />
            {flags
              .filter((f): f is FlagView & { y: number } => f.y !== null)
              .map((f, i) => (
                <ReferenceDot
                  key={i}
                  x={f.t}
                  y={f.y}
                  r={5}
                  fill={STRESS_META[f.level].color}
                  stroke={color.surface1}
                  strokeWidth={2}
                  ifOverflow="hidden"
                />
              ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
