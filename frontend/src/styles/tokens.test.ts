/** Keeps tokens.css (CSS) and tokens.ts (JS) in sync, and pins the stress thresholds. */
import { describe, expect, it } from 'vitest'
import css from './tokens.css?raw'
import { color, stressGradientStops, stressLevel } from './tokens.ts'

const camel = (kebab: string) => kebab.replace(/-(\w)/g, (_, c: string) => c.toUpperCase())

describe('design tokens', () => {
  it('every --color-* in tokens.css matches tokens.ts and vice versa', () => {
    const fromCss = Object.fromEntries(
      [...css.matchAll(/--color-([a-z0-9-]+):\s*(#[0-9a-f]{6});/gi)].map((m) => [camel(m[1]), m[2].toLowerCase()]),
    )
    expect(fromCss).toEqual(color)
  })

  it('applies PLAN.md thresholds: < 40 calm, 40 to 70 rising, > 70 high', () => {
    expect(stressLevel(null)).toBeNull()
    expect(stressLevel(0)).toBe('calm')
    expect(stressLevel(39.9)).toBe('calm')
    expect(stressLevel(40)).toBe('rising')
    expect(stressLevel(70)).toBe('rising')
    expect(stressLevel(70.1)).toBe('high')
  })

  it('places gradient zone edges at the thresholds', () => {
    const stops = stressGradientStops(100, 0)
    expect(stops.map((s) => s.offset)).toEqual([0, 0.3, 0.3, 0.6, 0.6, 1])
    // A curve that never leaves the calm zone is all green.
    expect(new Set(stressGradientStops(30, 10).map((s) => s.color))).toEqual(new Set([color.calm]))
  })
})
