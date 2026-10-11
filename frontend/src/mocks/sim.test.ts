import { describe, expect, it } from 'vitest'
import {
  computeBaseline,
  createIdGenerator,
  mulberry32,
  POPULATION_BASELINE,
  seededRng,
  stressScore,
  VitalsModel,
  watcherLevel,
} from './sim.ts'
import type { SimReading } from './sim.ts'

const baseline = { heart_rate: 72, breathing_rate: 14 }
const calm = { heart_rate: 72, breathing_rate: 14, confidence: 0.95 }

describe('stressScore (PLAN.md §3, data.md asserts)', () => {
  it('calm reading is about 0', () => {
    expect(stressScore(calm, baseline)).toBeCloseTo(0, 5)
  })

  it('+25% HR with breathing at baseline is 62.5 (heart rate is 0.5 of the 0.8 total weight)', () => {
    expect(stressScore({ ...calm, heart_rate: 90 }, baseline)).toBeCloseTo(62.5, 5)
  })

  it('everything maxed is 100', () => {
    expect(stressScore({ heart_rate: 120, breathing_rate: 25, confidence: 1 }, baseline)).toBeCloseTo(100, 5)
  })

  it('a missing input renormalizes the remaining weights', () => {
    // breathing missing: heart rate is the whole score
    expect(stressScore({ heart_rate: 90, confidence: 0.9 }, baseline)).toBeCloseTo(100, 5)
  })

  it('confidence under 0.5 is null', () => {
    expect(stressScore({ ...calm, confidence: 0.4 }, baseline)).toBeNull()
  })

  it('no baseline is null, and no inputs is null', () => {
    expect(stressScore(calm, null)).toBeNull()
    expect(stressScore({ confidence: 1 }, baseline)).toBeNull()
  })

  it('watcher thresholds are strict: > 70 high, > 40 rising', () => {
    expect(watcherLevel(70)).toBe('rising')
    expect(watcherLevel(70.1)).toBe('high')
    expect(watcherLevel(40)).toBeNull()
    expect(watcherLevel(null)).toBeNull()
  })
})

describe('PRNG + ids', () => {
  it('same seed gives the same sequence, different seeds differ', () => {
    const a = mulberry32(42)
    const b = mulberry32(42)
    const c = mulberry32(43)
    const seqA = Array.from({ length: 5 }, a)
    expect(Array.from({ length: 5 }, b)).toEqual(seqA)
    expect(Array.from({ length: 5 }, c)).not.toEqual(seqA)
    for (const x of seqA) {
      expect(x).toBeGreaterThanOrEqual(0)
      expect(x).toBeLessThan(1)
    }
  })

  it('ids are deterministic uuid v4 strings', () => {
    const ids1 = createIdGenerator(seededRng(7, 'ids'))
    const ids2 = createIdGenerator(seededRng(7, 'ids'))
    const first = ids1()
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(ids2()).toBe(first)
    expect(ids1()).not.toBe(first)
  })

  it('the vitals model is deterministic per seed', () => {
    const m1 = new VitalsModel(seededRng(1, 'vitals'))
    const m2 = new VitalsModel(seededRng(1, 'vitals'))
    for (let t = 0; t < 30_000; t += 1000) expect(m1.sample(t)).toEqual(m2.sample(t))
  })
})

describe('computeBaseline (data.md)', () => {
  const start = Date.parse('2026-10-10T18:00:00Z')
  const reading = (s: number, confidence = 0.95): SimReading => ({
    time: start + s * 1000,
    heart_rate: 70,
    breathing_rate: 13,
    expression: 'neutral',
    confidence,
    stress: null,
  })

  it('averages good readings after the 10 s warm-up', () => {
    const readings = [
      ...Array.from({ length: 10 }, (_, i) => ({ ...reading(i), heart_rate: 120 })),
      ...Array.from({ length: 20 }, (_, i) => reading(10 + i)),
      { ...reading(31, 0.2), heart_rate: 150 },
    ]
    const { baseline: b, fallback } = computeBaseline(readings, start, start + 40_000)
    expect(fallback).toBe(false)
    expect(b.n_samples).toBe(20)
    expect(b.heart_rate).toBe(70)
    expect(b.breathing_rate).toBe(13)
  })

  it('falls back to population defaults with fewer than 10 samples', () => {
    const readings = Array.from({ length: 18 }, (_, i) => reading(i))
    const { baseline: b, fallback } = computeBaseline(readings, start, start + 18_000)
    expect(fallback).toBe(true)
    expect(b.n_samples).toBe(8)
    expect(b.heart_rate).toBe(POPULATION_BASELINE.heart_rate)
    expect(b.breathing_rate).toBe(POPULATION_BASELINE.breathing_rate)
  })
})
