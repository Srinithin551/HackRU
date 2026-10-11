import { describe, expect, it } from 'vitest'
import { eyeScores, headAngles, isAway, LOOK_AWAY_MIN_MS, LookAwayTracker, RETURN_MS, type Gaze } from './lookAway.ts'

const facing: Gaze = { yaw: 3, pitch: 8, eyesSide: 0.1, eyesDown: 0.1 }

describe('isAway', () => {
  it('facing the screen is not away', () => {
    expect(isAway(facing)).toBe(false)
  })

  it('a turned head, sideways eyes, eyes in the lap or no face are away', () => {
    expect(isAway({ ...facing, yaw: -40 })).toBe(true)
    expect(isAway({ ...facing, pitch: 35 })).toBe(true)
    expect(isAway({ ...facing, eyesSide: 0.8 })).toBe(true)
    expect(isAway({ ...facing, eyesDown: 0.8 })).toBe(true)
    expect(isAway(null)).toBe(true)
  })
})

describe('headAngles', () => {
  const rotY = (deg: number) => {
    const r = (deg * Math.PI) / 180
    const [c, s] = [Math.cos(r), Math.sin(r)]
    return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1] // column-major
  }

  it('reads yaw from a head turned sideways, in either storage order', () => {
    expect(headAngles(rotY(40)).yaw).toBeCloseTo(40)
    expect(headAngles(rotY(-40)).yaw).toBeCloseTo(40)
    expect(headAngles(rotY(40)).pitch).toBeCloseTo(0)
  })
})

describe('eyeScores', () => {
  it('scores a sideways look from one eye out and the other in', () => {
    const scores: Record<string, number> = { eyeLookOutLeft: 0.9, eyeLookInRight: 0.7 }
    expect(eyeScores((n) => scores[n] ?? 0).eyesSide).toBeCloseTo(0.8)
  })
})

describe('LookAwayTracker', () => {
  it('reports a look-away once it has lasted long enough, and only once', () => {
    const t = new LookAwayTracker()
    expect(t.update(0, true)).toBe(false)
    expect(t.update(LOOK_AWAY_MIN_MS - 1, true)).toBe(false)
    expect(t.update(LOOK_AWAY_MIN_MS, true)).toBe(true)
    expect(t.update(LOOK_AWAY_MIN_MS + 2_000, true)).toBe(false)
  })

  it('ignores a quick glance', () => {
    const t = new LookAwayTracker()
    t.update(0, true)
    t.update(500, false)
    expect(t.update(500 + RETURN_MS, false)).toBe(false)
    expect(t.update(2_000, true)).toBe(false) // a fresh episode starts here
  })

  it('a flicker back to the screen does not split one look-away into two', () => {
    const t = new LookAwayTracker()
    t.update(0, true)
    t.update(1_000, false) // detector noise
    expect(t.update(1_200, true)).toBe(false)
    expect(t.update(LOOK_AWAY_MIN_MS, true)).toBe(true)
  })

  it('looking back for a while ends the episode, so the next look-away counts again', () => {
    const t = new LookAwayTracker()
    t.update(0, true)
    t.update(LOOK_AWAY_MIN_MS, true)
    t.update(2_000, false)
    t.update(2_000 + RETURN_MS, false)
    t.update(5_000, true)
    expect(t.update(5_000 + LOOK_AWAY_MIN_MS, true)).toBe(true)
  })
})
