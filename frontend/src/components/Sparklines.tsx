/**
 * The live 60-second mini charts on the vitals panel. Two separate charts (heart
 * rate in bpm, stress 0-100) instead of one chart with two y-axes, which would
 * invent correlations. Animation is off: new points arrive every second and the
 * chart should just extend, not re-animate.
 */
import { useId } from 'react'
import { Area, AreaChart, Line, LineChart, ReferenceLine, ResponsiveContainer, XAxis, YAxis } from 'recharts'
import type { VitalsSample } from '../hooks/useLiveSession.ts'
import { color, STRESS_HIGH, STRESS_RISING, stressColor, stressGradientStops } from '../styles/tokens.ts'

const MARGIN = { top: 4, right: 2, bottom: 4, left: 2 }
const isNum = (v: number | null): v is number => typeof v === 'number'
/** useId() returns ids with colons, which break url(#...) references. */
const svgId = (id: string) => id.replace(/[^a-zA-Z0-9_-]/g, '')

export function HrSparkline({ data }: { data: VitalsSample[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={MARGIN}>
        <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} hide />
        <YAxis domain={['dataMin - 4', 'dataMax + 4']} hide />
        <Line dataKey="hr" type="monotone" stroke={color.seriesHr} strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}

export function StressSparkline({ data }: { data: VitalsSample[] }) {
  const id = svgId(useId())
  const values = data.map((d) => d.stress).filter(isNum)
  const max = values.length ? Math.max(...values) : 0
  const min = values.length ? Math.min(...values) : 0
  // A gradient over a zero-height line doesn't render, so flat lines get a solid color.
  const flat = max - min < 0.5
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={MARGIN}>
        <defs>
          <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="0" y2="1">
            {stressGradientStops(max, 0).map((s, i) => (
              <stop key={i} offset={s.offset} stopColor={s.color} stopOpacity={0.2} />
            ))}
          </linearGradient>
          <linearGradient id={`${id}-line`} x1="0" y1="0" x2="0" y2="1">
            {stressGradientStops(max, min).map((s, i) => (
              <stop key={i} offset={s.offset} stopColor={s.color} />
            ))}
          </linearGradient>
        </defs>
        <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} hide />
        <YAxis domain={[0, 100]} hide />
        <ReferenceLine y={STRESS_RISING} stroke={color.borderStrong} strokeDasharray="3 4" />
        <ReferenceLine y={STRESS_HIGH} stroke={color.borderStrong} strokeDasharray="3 4" />
        <Area
          dataKey="stress"
          type="monotone"
          baseValue={0}
          stroke={flat ? stressColor(max) : `url(#${id}-line)`}
          strokeWidth={2}
          fill={`url(#${id}-fill)`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
