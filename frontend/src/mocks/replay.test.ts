import { beforeAll, describe, expect, it } from 'vitest'
import type { CoachFlagEvent, ReplayResponse, Report } from '../types.ts'
import { MockSessionEngine } from './engine.ts'
import { BUCKET_MS, buildReplay } from './replay.ts'
import { buildReport } from './report.ts'
import { VirtualScheduler } from './scheduler.ts'

let replay: ReplayResponse
let report: Report
let flags: CoachFlagEvent[]

beforeAll(async () => {
  const scheduler = new VirtualScheduler(Date.parse('2026-10-10T18:00:00Z'))
  const engine = new MockSessionEngine({
    sessionId: 'replay-test',
    userName: 'Tester',
    interviewType: 'software_engineering',
    roleTitle: 'Frontend Engineer',
    seed: 20261010,
    scheduler,
    autoAnswer: true,
  })
  engine.start()
  const ended = await scheduler.runUntil(() => engine.phase === 'ended', 30 * 60_000)
  expect(ended).toBe(true)
  const recording = engine.getRecording()
  flags = recording.flags
  replay = buildReplay(recording)
  report = buildReport(replay, flags)
})

describe('buildReplay', () => {
  it('has session meta with a real baseline (no n_samples)', () => {
    expect(replay.session.id).toBe('replay-test')
    expect(replay.session.user_name).toBe('Tester')
    expect(replay.session.ended_at).not.toBeNull()
    expect(replay.session.baseline).not.toBeNull()
    expect(Object.keys(replay.session.baseline ?? {}).sort()).toEqual(['breathing_rate', 'focus', 'heart_rate'])
  })

  it('series is 5 s buckets, aligned to epoch multiples and ordered', () => {
    expect(replay.series.length).toBeGreaterThan(20)
    let previous = -Infinity
    for (const point of replay.series) {
      expect(point.t.endsWith('Z')).toBe(true)
      const t = Date.parse(point.t)
      expect(t % BUCKET_MS).toBe(0)
      expect(t).toBeGreaterThan(previous)
      previous = t
      if (point.stress !== null && point.stress_peak !== null) expect(point.stress_peak).toBeGreaterThanOrEqual(point.stress)
    }
    expect(replay.series.some((p) => (p.stress_peak ?? 0) > 70)).toBe(true)
    // The calibration rows were backfilled once the baseline existed.
    expect(replay.series[2].stress).not.toBeNull()
  })

  it('questions are ordered by idx with consistent stats', () => {
    const qs = replay.questions
    expect(qs[0].kind).toBe('calibration')
    expect(qs[0].idx).toBe(0)
    for (let i = 1; i < qs.length; i++) expect(qs[i].idx).toBeGreaterThan(qs[i - 1].idx)
    expect(qs.some((q) => q.kind === 'follow_up')).toBe(true)

    for (const q of qs) {
      const s = q.stats
      expect(s).not.toBeNull()
      if (!s) continue
      expect(s.question_id).toBe(q.id)
      expect(q.answer_started_at).not.toBeNull()
      expect(q.answered_at).not.toBeNull()
      expect(Date.parse(q.answered_at ?? '')).toBeGreaterThan(Date.parse(q.answer_started_at ?? ''))
      expect(s.n_samples).toBeGreaterThan(0)
      expect(s.n_samples).toBeLessThanOrEqual(Math.ceil(s.duration_s) + 1)
      expect(s.hr_peak ?? 0).toBeGreaterThanOrEqual(s.hr_mean ?? 0)
      expect(s.stress_peak ?? 0).toBeGreaterThanOrEqual(s.stress_mean ?? 0)
      if (s.peak_offset_s !== null) {
        expect(s.peak_offset_s).toBeGreaterThanOrEqual(0)
        expect(s.peak_offset_s).toBeLessThanOrEqual(s.duration_s)
      }
    }

    const hardest = qs.reduce((a, b) => ((b.stats?.stress_peak ?? 0) > (a.stats?.stress_peak ?? 0) ? b : a))
    expect(hardest.kind).toBe('main')
    expect(hardest.stats?.stress_peak ?? 0).toBeGreaterThan(70)
    expect(hardest.stats?.hr_delta_pct ?? 0).toBeGreaterThan(5)
  })

  it('includes the coach flags extension', () => {
    expect(replay.coach_flags).toEqual(flags.map((f) => ({ ts: f.ts, level: f.level, message: f.message })))
    expect((replay.coach_flags ?? []).some((f) => f.level === 'high')).toBe(true)
  })
})

describe('buildReport', () => {
  it('matches the contract and cites numbers', () => {
    const ids = new Set(replay.questions.map((q) => q.id))
    const calibrationId = replay.questions[0].id
    expect(report.per_question.length).toBe(replay.questions.length - 1)
    for (const feedback of report.per_question) {
      expect(ids.has(feedback.question_id)).toBe(true)
      expect(feedback.question_id).not.toBe(calibrationId)
      expect(feedback.body_signal).toMatch(/\d/)
      expect(feedback.what_happened.length).toBeGreaterThan(0)
      expect(feedback.advice.length).toBeGreaterThan(0)
    }
    expect(report.top_tips).toHaveLength(3)
    expect(report.composure_score).toBeGreaterThanOrEqual(0)
    expect(report.composure_score).toBeLessThanOrEqual(100)
    expect(Number.isInteger(report.composure_score)).toBe(true)
    expect(report.hardest_moment).toMatch(/\d/)
    expect(report.summary.split(/(?<=\.)\s+/).length).toBeGreaterThanOrEqual(2)
  })

  it('is deterministic', () => {
    expect(buildReport(replay, flags)).toEqual(report)
  })
})
