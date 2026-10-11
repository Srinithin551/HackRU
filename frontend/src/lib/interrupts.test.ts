/** The three rules an interrupt must obey: reset, never repeat, comply first. */
import { describe, expect, it } from 'vitest'
import { coachingInterrupt, nextInterrupt } from './interrupts.ts'
import type { CoachFlagEvent, QuestionEvent } from '../types.ts'

const flag = (message: string, over: Partial<CoachFlagEvent> = {}): CoachFlagEvent => ({
  ts: '2026-10-11T12:00:00Z',
  level: 'rising',
  message,
  command: 'take one slow breath',
  ...over,
})

const question = (over: Partial<QuestionEvent> = {}): QuestionEvent => ({
  question_id: 'q1',
  index: 1,
  total: 3,
  kind: 'main',
  text: 'Tell me about a project.',
  audio_url: null,
  ...over,
})

describe('nextInterrupt', () => {
  it('turns the first unseen flag into an interrupt carrying its command', () => {
    const shown = new Set<string>()
    const got = nextInterrupt([flag('Your pulse climbs 20% above your calm.')], shown, 'Judge')
    expect(got).toMatchObject({
      kind: 'rising',
      speaker: 'Judge',
      message: 'Your pulse climbs 20% above your calm.',
      command: 'take one slow breath',
    })
  })

  it('never shows the same text twice', () => {
    const shown = new Set<string>()
    const first = nextInterrupt([flag('Your breath shortens.')], shown, 'Apollo')
    expect(first).not.toBeNull()
    // The identical message arriving again is swallowed, not re-displayed.
    expect(nextInterrupt([flag('Your breath shortens.')], shown, 'Apollo')).toBeNull()
  })

  it('skips repeats but still surfaces a genuinely new flag in the same batch', () => {
    const shown = new Set(['Seen before.'])
    const got = nextInterrupt([flag('Seen before.'), flag('Brand new.')], shown, 'Hera')
    expect(got?.message).toBe('Brand new.')
  })

  it('shows only the newest correction, leaving the rest unrecorded', () => {
    const shown = new Set<string>()
    nextInterrupt([flag('First.'), flag('Second.')], shown, 'Hermes')
    // "Second." was never displayed, so it stays available for a later flag.
    expect(shown.has('Second.')).toBe(false)
  })

  it('falls back to a command when the backend sent none', () => {
    const got = nextInterrupt([flag('Steady yourself.', { command: undefined })], new Set(), 'Judge')
    expect(got?.command).toBeTruthy()
  })

  it('ignores blank messages', () => {
    expect(nextInterrupt([flag('   ')], new Set(), 'Judge')).toBeNull()
  })
})

describe('coachingInterrupt', () => {
  it('raises coaching attached to a question, with a compliance command', () => {
    const got = coachingInterrupt(question({ coaching: 'That missed the question.' }), new Set(), 'Judge')
    expect(got).toMatchObject({ kind: 'coaching', message: 'That missed the question.' })
    expect(got?.command).toBeTruthy()
  })

  it('raises nothing for a question without coaching', () => {
    expect(coachingInterrupt(question(), new Set(), 'Judge')).toBeNull()
    expect(coachingInterrupt(null, new Set(), 'Judge')).toBeNull()
  })

  it('does not repeat coaching already shown', () => {
    const shown = new Set<string>()
    coachingInterrupt(question({ coaching: 'Be specific.' }), shown, 'Judge')
    expect(coachingInterrupt(question({ question_id: 'q2', coaching: 'Be specific.' }), shown, 'Apollo')).toBeNull()
  })
})
