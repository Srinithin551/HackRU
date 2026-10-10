/**
 * Semicircle stress gauge, 0 to 100. The track is split into the three zones
 * (calm < 40, rising 40-70, high > 70) at low opacity so you can see where the
 * thresholds are; the value arc is drawn on top in the current zone's color,
 * and the number + badge underneath say it in words. Tweened so it glides.
 */
import { useTweenedNumber } from '../hooks/useTweenedNumber.ts'
import { color, STRESS_HIGH, STRESS_RISING, stressColor } from '../styles/tokens.ts'
import { StressBadge } from './ui.tsx'

const CX = 100
const CY = 100
const R = 80
const STROKE = 14

/** Point on the arc for a value 0..100 (0 = left end, 100 = right end). */
function point(value: number): [number, number] {
  const angle = Math.PI * (1 - value / 100)
  return [CX + R * Math.cos(angle), CY - R * Math.sin(angle)]
}

function arc(from: number, to: number): string {
  const [x0, y0] = point(from)
  const [x1, y1] = point(to)
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${R} ${R} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`
}

const ZONES = [
  { from: 0, to: STRESS_RISING - 0.8, color: color.calm },
  { from: STRESS_RISING + 0.8, to: STRESS_HIGH - 0.8, color: color.rising },
  { from: STRESS_HIGH + 0.8, to: 100, color: color.high },
]

export function StressGauge({ score, emptyLabel }: { score: number | null; emptyLabel: string }) {
  const shown = useTweenedNumber(score === null ? null : Math.max(0, Math.min(100, score)))
  const value = shown ?? 0
  const [nx, ny] = point(value)

  return (
    <div className="mx-auto w-full max-w-64">
      <div className="relative">
      <svg viewBox="0 0 200 108" className="w-full" role="img" aria-label={score === null ? emptyLabel : `Stress ${Math.round(score)} of 100`}>
        {ZONES.map((z) => (
          <path key={z.from} d={arc(z.from, z.to)} fill="none" stroke={z.color} strokeOpacity={0.2} strokeWidth={STROKE} />
        ))}
        {shown !== null && value > 0.5 && (
          <path d={arc(0, value)} fill="none" stroke={stressColor(value)} strokeWidth={STROKE} strokeLinecap="round" />
        )}
        {shown !== null && <circle cx={nx} cy={ny} r={8} fill={color.ink} stroke={color.surface1} strokeWidth={3} />}
        {[STRESS_RISING, STRESS_HIGH].map((t) => {
          const [x, y] = point(t)
          const [ox, oy] = [(x - CX) * 0.74 + CX, (y - CY) * 0.74 + CY]
          return (
            <text key={t} x={ox} y={oy} textAnchor="middle" dominantBaseline="middle" fontSize="9" fill={color.inkMuted} fontFamily="var(--font-mono)">
              {t}
            </text>
          )
        })}
      </svg>
      <div className="absolute inset-x-0 bottom-0 flex justify-center">
        <span className="text-metric font-semibold tabular-nums tracking-tight">{shown === null ? '—' : Math.round(value)}</span>
      </div>
      </div>
      <div className="mt-1.5 flex justify-center">
        <StressBadge score={score} empty={emptyLabel} />
      </div>
    </div>
  )
}
