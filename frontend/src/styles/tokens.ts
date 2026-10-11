/**
 * Design tokens for JavaScript: Recharts strokes, SVG gauges, gradients. CSS uses
 * tokens.css; these hex values must match it (tokens.test.ts enforces that).
 *
 * Also home to the stress-scale semantics every view shares (PLAN.md §3):
 * < 40 calm, 40 to 70 rising, > 70 high. The thresholds are fixed team-wide.
 */

export const color = {
  page: '#000000',
  surface1: '#0d0d10',
  surface2: '#16161b',
  surface3: '#202026',
  hairline: '#2a2a32',
  borderStrong: '#44444f',
  ink: '#ffffff',
  inkSecondary: '#c9c9d2',
  inkMuted: '#8c8c97',
  inkDisabled: '#55555f',
  accent: '#d4af37',
  accentHover: '#e8c662',
  accentPressed: '#b08f26',
  accentSubtle: '#2a2110',
  onAccent: '#0a0a0a',
  gold: '#d4af37',
  goldSubtle: '#2a2110',
  pale: '#c9ccd4',
  paleDeep: '#8f96a3',
  paleSubtle: '#1a1c22',
  calm: '#34d399',
  rising: '#f5b83d',
  high: '#f0524a',
  calmSubtle: '#0f2a22',
  risingSubtle: '#2e2410',
  highSubtle: '#331614',
  seriesHr: '#d4af37',
  seriesBreathing: '#8f96a3',
  chartGrid: '#1c1c22',
  chartAxis: '#8c8c97',
} as const

export const STRESS_RISING = 40
export const STRESS_HIGH = 70

export type StressLevel = 'calm' | 'rising' | 'high'

/** PLAN.md: < 40 calm, 40 to 70 rising (inclusive), > 70 high. Null stays null (no baseline / no face). */
export function stressLevel(score: number | null | undefined): StressLevel | null {
  if (score === null || score === undefined || Number.isNaN(score)) return null
  if (score > STRESS_HIGH) return 'high'
  if (score >= STRESS_RISING) return 'rising'
  return 'calm'
}

/** Status colors always ship with an icon shape and a word, never color alone (color-blind safe). */
export const STRESS_META: Record<StressLevel, { label: string; color: string; subtle: string; icon: 'circle' | 'triangle' | 'diamond' }> = {
  calm: { label: 'Calm', color: color.calm, subtle: color.calmSubtle, icon: 'circle' },
  rising: { label: 'Rising', color: color.rising, subtle: color.risingSubtle, icon: 'triangle' },
  high: { label: 'High', color: color.high, subtle: color.highSubtle, icon: 'diamond' },
}

export const stressColor = (score: number | null | undefined): string => {
  const level = stressLevel(score)
  return level ? STRESS_META[level].color : color.inkDisabled
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/**
 * Hard-edged vertical gradient stops that paint a stress curve by zone (red above 70,
 * amber 40-70, green below 40). SVG gradients span the shape's bounding box, so pass
 * the value at the box's top and bottom: for a line that's [max, min] of the data,
 * for an area filled down to 0 it's [max, 0].
 */
export function stressGradientStops(top: number, bottom: number): { offset: number; color: string }[] {
  const span = top - bottom
  if (!(span > 0)) {
    const c = stressColor(top)
    return [
      { offset: 0, color: c },
      { offset: 1, color: c },
    ]
  }
  const at = (value: number) => clamp01((top - value) / span)
  const high = at(STRESS_HIGH)
  const rising = at(STRESS_RISING)
  // Each zone becomes two stops with the same color (hard edges); zones the curve never reaches are dropped.
  return [
    { from: 0, to: high, color: color.high },
    { from: high, to: rising, color: color.rising },
    { from: rising, to: 1, color: color.calm },
  ]
    .filter((band) => band.to > band.from)
    .flatMap((band) => [
      { offset: band.from, color: band.color },
      { offset: band.to, color: band.color },
    ])
}

export const motion = {
  /** Number/gauge tween: fast enough to feel live at 1 Hz, slow enough to read as motion. */
  tweenMs: 600,
} as const
