/**
 * Design tokens for JavaScript: Recharts strokes, SVG gauges, gradients. CSS uses
 * tokens.css; these hex values must match it (tokens.test.ts enforces that).
 *
 * Also home to the stress-scale semantics every view shares (PLAN.md §3):
 * < 40 calm, 40 to 70 rising, > 70 high. The thresholds are fixed team-wide.
 */

export const color = {
  page: '#0e1217',
  surface1: '#151a21',
  surface2: '#1c222b',
  surface3: '#242b36',
  hairline: '#262e39',
  borderStrong: '#3a4554',
  ink: '#eef2f6',
  inkSecondary: '#aeb8c4',
  inkMuted: '#7e8a97',
  inkDisabled: '#4b5563',
  accent: '#7c8cff',
  accentHover: '#96a3ff',
  accentPressed: '#6475f0',
  accentSubtle: '#1d2242',
  onAccent: '#0b0e1a',
  calm: '#34d399',
  rising: '#f5b83d',
  high: '#f0524a',
  calmSubtle: '#0f2a22',
  risingSubtle: '#2e2410',
  highSubtle: '#331614',
  seriesHr: '#7c8cff',
  seriesBreathing: '#e58fd2',
  seriesFocus: '#9fe1f0',
  chartGrid: '#1e252e',
  chartAxis: '#7e8a97',
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
