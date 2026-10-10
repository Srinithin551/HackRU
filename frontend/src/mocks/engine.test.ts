import { describe, expect, it } from 'vitest'
import type { CoachFlagEvent, QuestionEvent, ServerEvent } from '../types.ts'
import { MockSessionEngine } from './engine.ts'
import type { EngineConfig } from './engine.ts'
import { VirtualScheduler } from './scheduler.ts'

const START = Date.parse('2026-10-10T18:00:00Z')
const MAX_MS = 20 * 60_000

function setup(overrides: Partial<EngineConfig> = {}) {
  const scheduler = new VirtualScheduler(START)
  const engine = new MockSessionEngine({
    sessionId: 'test-session',
    userName: 'Tester',
    interviewType: 'demo',
    roleTitle: 'Software Engineer',
    seed: 7,
    scheduler,
    ...overrides,
  })
  const events: ServerEvent[] = []
  engine.subscribe((event) => events.push(event))
  return { scheduler, engine, events }
}

async function runAuto(seed = 7) {
  const ctx = setup({ seed, autoAnswer: true })
  ctx.engine.start()
  const ended = await ctx.scheduler.runUntil(() => ctx.engine.phase === 'ended', MAX_MS)
  return { ...ctx, ended }
}

const questionsOf = (events: ServerEvent[]): QuestionEvent[] =>
  events.flatMap((e) => (e.type === 'question' ? [e.data] : []))
const flagsOf = (events: ServerEvent[]): CoachFlagEvent[] =>
  events.flatMap((e) => (e.type === 'coach_flag' ? [e.data] : []))
/** The interview flow without the 1 Hz vitals and the coach flags. */
const flowOf = (events: ServerEvent[]) => events.filter((e) => e.type !== 'vitals' && e.type !== 'coach_flag')

describe('MockSessionEngine (demo, auto-answer)', () => {
  it('runs to session_ended and cleans up every timer', async () => {
    const { ended, engine, scheduler } = await runAuto()
    expect(ended).toBe(true)
    expect(engine.phase).toBe('ended')
    expect(scheduler.pending).toBe(0)
  })

  it('starts with state calibrating and asks calibration, main, main, follow_up, main', async () => {
    const { events } = await runAuto()
    expect(events[0]).toEqual({ type: 'state', data: { state: 'calibrating' } })
    const questions = questionsOf(events)
    expect(questions.map((q) => q.kind)).toEqual(['calibration', 'main', 'main', 'follow_up', 'main'])
    expect(questions[0].index).toBe(0)
    for (let i = 1; i < questions.length; i++) expect(questions[i].index).toBeGreaterThan(questions[i - 1].index)
    for (const q of questions) expect(q.total).toBe(3)
    expect(questions[questions.length - 1].index).toBeGreaterThan(3)
    expect(new Set(questions.map((q) => q.question_id)).size).toBe(questions.length)
  })

  it('broadcasts listening right after every question', async () => {
    const flow = flowOf((await runAuto()).events)
    flow.forEach((event, i) => {
      if (event.type === 'question') expect(flow[i + 1]).toEqual({ type: 'state', data: { state: 'listening' } })
    })
  })

  it('emits about one vitals event per second', async () => {
    const { events, engine } = await runAuto()
    const { started_at, ended_at } = engine.getRecording().session
    const seconds = ((ended_at ?? 0) - (started_at ?? 0)) / 1000
    const vitals = events.filter((e) => e.type === 'vitals').length
    expect(Math.abs(vitals - seconds)).toBeLessThanOrEqual(1)
  })

  it('stress is null before the baseline and numeric after it', async () => {
    const { events, engine } = await runAuto()
    const calibration = engine.getRecording().questions[0]
    expect(calibration.kind).toBe('calibration')
    const baselineAt = calibration.answered_at ?? 0
    const firstMain = events.findIndex((e) => e.type === 'question' && e.data.kind === 'main')
    let before = 0
    let after = 0
    events.forEach((e, i) => {
      if (e.type !== 'vitals') return
      if (Date.parse(e.data.ts) < baselineAt) {
        expect(e.data.stress).toBeNull()
        before++
      } else if (i > firstMain) {
        expect(typeof e.data.stress).toBe('number')
        after++
      }
    })
    expect(before).toBeGreaterThan(20)
    expect(after).toBeGreaterThan(20)
    expect(engine.getRecording().session.baselineFallback).toBe(false)
  })

  it('the spike question raises a high coach flag, and flags are at least 12 s apart', async () => {
    const { events, engine } = await runAuto()
    const flags = flagsOf(events)
    const spikeQuestion = questionsOf(events)[2]
    const recorded = engine.getRecording().flags
    expect(recorded.some((f) => f.level === 'high' && f.question_id === spikeQuestion.question_id)).toBe(true)
    for (let i = 1; i < flags.length; i++) {
      expect(Date.parse(flags[i].ts) - Date.parse(flags[i - 1].ts)).toBeGreaterThanOrEqual(12_000)
    }
    for (const f of flags) expect(f.message).toMatch(/\d/)
  })

  it('plays a filler and goes to thinking after every answer, then ends with done → session_ended', async () => {
    const { events } = await runAuto()
    const flow = flowOf(events)
    flow.forEach((event, i) => {
      if (event.type !== 'question' || event.data.kind === 'calibration') return
      expect(flow[i + 2].type).toBe('filler')
      expect(flow[i + 3]).toEqual({ type: 'state', data: { state: 'thinking' } })
    })
    const tail = flow.slice(-3)
    expect(tail[0].type).toBe('filler')
    expect(tail[1]).toEqual({ type: 'state', data: { state: 'done' } })
    expect(tail[2]).toEqual({
      type: 'session_ended',
      data: { report_url: '/api/sessions/test-session/report', replay_url: '/api/sessions/test-session/replay' },
    })
    expect(events[events.length - 1].type).toBe('session_ended')
  })

  it('never plays the same filler twice in a row', async () => {
    const { events } = await runAuto()
    const fillers = events.flatMap((e) => (e.type === 'filler' ? [e.data.audio_url] : []))
    for (let i = 1; i < fillers.length; i++) expect(fillers[i]).not.toBe(fillers[i - 1])
  })

  it('is deterministic for the same seed', async () => {
    const a = await runAuto(11)
    const b = await runAuto(11)
    expect(JSON.stringify(b.events)).toBe(JSON.stringify(a.events))
    expect(JSON.stringify(b.engine.getRecording())).toBe(JSON.stringify(a.engine.getRecording()))
  })
})

describe('MockSessionEngine (manual client)', () => {
  it('start() is idempotent', async () => {
    const { engine, events, scheduler } = setup()
    engine.start()
    engine.start()
    await scheduler.advance(2_000)
    expect(questionsOf(events)).toHaveLength(1)
    expect(events.filter((e) => e.type === 'state' && e.data.state === 'calibrating')).toHaveLength(1)
    engine.stop()
  })

  it('end_session ends early: no more questions, done, then session_ended', async () => {
    const { engine, events, scheduler } = setup()
    engine.start()
    await scheduler.advance(5_000)
    engine.receive({ type: 'end_session', data: {} })
    expect(engine.phase).toBe('done')
    const ended = await scheduler.runUntil(() => engine.phase === 'ended', 60_000)
    expect(ended).toBe(true)
    expect(questionsOf(events)).toHaveLength(1)
    const flow = flowOf(events)
    expect(flow.slice(-2).map((e) => e.type)).toEqual(['state', 'session_ended'])
    expect(flow[flow.length - 2]).toEqual({ type: 'state', data: { state: 'done' } })
    expect(scheduler.pending).toBe(0)
  })

  it('ignores duplicate answer_done and events for other question ids', async () => {
    const { engine, events, scheduler } = setup()
    engine.start()
    await scheduler.advance(2_500)
    const calibrationId = questionsOf(events)[0].question_id
    engine.receive({ type: 'answer_started', data: { question_id: calibrationId } })
    await scheduler.advance(38_000)
    engine.receive({ type: 'answer_done', data: { question_id: calibrationId, transcript: 'Going well.' } })

    const q1 = questionsOf(events)[1]
    expect(q1.kind).toBe('main')
    engine.receive({ type: 'answer_done', data: { question_id: calibrationId, transcript: 'late duplicate' } })
    engine.receive({ type: 'answer_started', data: { question_id: q1.question_id } })
    await scheduler.advance(15_000)
    engine.receive({ type: 'answer_done', data: { question_id: 'not-a-question', transcript: 'x' } })
    expect(events.filter((e) => e.type === 'filler')).toHaveLength(0)
    engine.receive({ type: 'answer_done', data: { question_id: q1.question_id, transcript: 'First answer.' } })
    engine.receive({ type: 'answer_done', data: { question_id: q1.question_id, transcript: 'Duplicate.' } })
    expect(events.filter((e) => e.type === 'filler')).toHaveLength(1)

    await scheduler.advance(2_000)
    expect(questionsOf(events)).toHaveLength(3)
    const recorded = engine.getRecording().questions
    expect(recorded[0].transcript).toBe('Going well.')
    expect(recorded[1].transcript).toBe('First answer.')
    engine.stop()
    expect(scheduler.pending).toBe(0)
  })

  it('treats a 120 s silence as an empty answer', async () => {
    const { engine, events, scheduler } = setup()
    engine.start()
    await scheduler.advance(121_000)
    expect(questionsOf(events).map((q) => q.kind)).toEqual(['calibration', 'main'])
    expect(engine.getRecording().questions[0].transcript).toBe('')
    engine.stop()
  })

  it('a throwing listener does not break the engine', async () => {
    const { engine, events, scheduler } = setup()
    const originalError = console.error
    console.error = () => {}
    try {
      engine.subscribe(() => {
        throw new Error('boom')
      })
      engine.start()
      await scheduler.advance(3_000)
    } finally {
      console.error = originalError
    }
    expect(events.filter((e) => e.type === 'vitals')).toHaveLength(3)
    engine.stop()
  })
})
