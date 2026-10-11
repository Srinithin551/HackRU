/**
 * Contract check: Data's replay_sample.json (exported from a real Tiger Data session)
 * must keep the shape src/types.ts expects. If Data changes /replay, this fails
 * instead of the replay chart silently breaking.
 */
import { describe, expect, it } from 'vitest'
import type { ReplayResponse } from './types.ts'
import sample from './mocks/replay_sample.json'

const replay = sample as unknown as ReplayResponse
const isNumOrNull = (v: unknown) => v === null || typeof v === 'number'
const isIso = (v: unknown) => typeof v === 'string' && !Number.isNaN(Date.parse(v))

describe("Data's replay_sample.json matches ReplayResponse", () => {
  it('has session metadata with a baseline', () => {
    expect(typeof replay.session.id).toBe('string')
    expect(isIso(replay.session.started_at)).toBe(true)
    expect(replay.session.baseline).not.toBeNull()
    for (const key of ['heart_rate', 'breathing_rate'] as const) {
      expect(typeof replay.session.baseline?.[key]).toBe('number')
    }
  })

  it('has an ordered 5-second series of numbers or nulls', () => {
    expect(replay.series.length).toBeGreaterThan(10)
    let prev = -Infinity
    for (const p of replay.series) {
      expect(isIso(p.t)).toBe(true)
      const t = Date.parse(p.t)
      expect(t).toBeGreaterThan(prev)
      prev = t
      for (const key of ['hr', 'br', 'stress', 'stress_peak'] as const) expect(isNumOrNull(p[key])).toBe(true)
    }
  })

  it('has questions with valid kinds, windows and stats', () => {
    expect(replay.questions.length).toBeGreaterThan(0)
    for (const q of replay.questions) {
      expect(['calibration', 'main', 'follow_up']).toContain(q.kind)
      expect(typeof q.idx).toBe('number')
      expect(typeof q.text).toBe('string')
      if (q.answer_started_at) expect(isIso(q.answer_started_at)).toBe(true)
      if (q.answered_at) expect(isIso(q.answered_at)).toBe(true)
      if (q.stats) {
        expect(typeof q.stats.question_id).toBe('string')
        expect(isNumOrNull(q.stats.stress_peak)).toBe(true)
        expect(isNumOrNull(q.stats.hr_mean)).toBe(true)
      }
    }
  })
})
