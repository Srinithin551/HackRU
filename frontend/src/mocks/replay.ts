/**
 * Builds GET /api/sessions/{id}/replay from an engine recording, mirroring Data:
 * `series` is the vitals_5s continuous aggregate, question stats follow
 * get_question_stats (data.md).
 *
 * DOM-free.
 */
import type { QuestionStats, ReplayPoint, ReplayQuestion, ReplayResponse } from '../types.ts'
import type { RecordedQuestion, RecordedReading, SessionRecording } from './engine.ts'
import { isoUtc, maxOf, mean, MIN_CONFIDENCE, roundOrNull, round } from './sim.ts'
import type { BaselineValues } from './sim.ts'

export const BUCKET_MS = 5_000

/** time_bucket('5 seconds') + avg/max, over every row (SQL avg skips nulls). */
export function buildSeries(readings: readonly RecordedReading[]): ReplayPoint[] {
  const buckets = new Map<number, RecordedReading[]>()
  for (const r of readings) {
    const bucket = Math.floor(r.time / BUCKET_MS) * BUCKET_MS
    const rows = buckets.get(bucket)
    if (rows) rows.push(r)
    else buckets.set(bucket, [r])
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => a - b)
    .map(([bucket, rows]) => ({
      t: isoUtc(bucket),
      hr: roundOrNull(mean(rows.map((r) => r.heart_rate)), 1),
      br: roundOrNull(mean(rows.map((r) => r.breathing_rate)), 1),
      focus: roundOrNull(mean(rows.map((r) => r.focus)), 2),
      stress: roundOrNull(mean(rows.map((r) => r.stress)), 1),
      stress_peak: roundOrNull(maxOf(rows.map((r) => r.stress)), 1),
    }))
}

/**
 * get_question_stats: rows with confidence >= 0.5 between
 * COALESCE(answer_started_at, created_at) and COALESCE(answered_at, now).
 */
export function questionStats(
  question: RecordedQuestion,
  readings: readonly RecordedReading[],
  baseline: BaselineValues | null,
  nowMs: number,
): QuestionStats {
  const from = question.answer_started_at ?? question.created_at
  const to = question.answered_at ?? nowMs
  const rows = readings.filter((r) => r.time >= from && r.time <= to && r.confidence >= MIN_CONFIDENCE)
  const hrMean = mean(rows.map((r) => r.heart_rate))
  const stressPeak = maxOf(rows.map((r) => r.stress))
  // array_agg(time ORDER BY stress DESC NULLS LAST)[1]: the earliest row at the peak.
  const peakRow = stressPeak === null ? undefined : rows.find((r) => r.stress === stressPeak)
  const peakOffset =
    peakRow && question.answer_started_at !== null ? (peakRow.time - question.answer_started_at) / 1000 : null
  return {
    question_id: question.id,
    duration_s: round(Math.max(0, to - from) / 1000, 1),
    n_samples: rows.length,
    hr_mean: roundOrNull(hrMean, 1),
    hr_peak: roundOrNull(maxOf(rows.map((r) => r.heart_rate)), 1),
    hr_delta_pct:
      hrMean !== null && baseline ? round(((hrMean - baseline.heart_rate) / baseline.heart_rate) * 100, 1) : null,
    br_mean: roundOrNull(mean(rows.map((r) => r.breathing_rate)), 1),
    focus_mean: roundOrNull(mean(rows.map((r) => r.focus)), 2),
    stress_mean: roundOrNull(mean(rows.map((r) => r.stress)), 1),
    stress_peak: roundOrNull(stressPeak, 1),
    peak_offset_s: roundOrNull(peakOffset, 1),
  }
}

/**
 * The full replay payload. `nowMs` stands in for SQL now() on unanswered
 * questions; it defaults to the session end (or the last reading).
 */
export function buildReplay(recording: SessionRecording, nowMs?: number): ReplayResponse {
  const { session, readings, flags } = recording
  const lastReading = readings.length > 0 ? readings[readings.length - 1].time : null
  const now = nowMs ?? session.ended_at ?? lastReading ?? session.started_at ?? 0
  const baseline = session.baseline

  const questions: ReplayQuestion[] = [...recording.questions]
    .sort((a, b) => a.idx - b.idx)
    .map((q) => ({
      id: q.id,
      idx: q.idx,
      kind: q.kind,
      text: q.text,
      transcript: q.transcript,
      answer_started_at: q.answer_started_at === null ? null : isoUtc(q.answer_started_at),
      answered_at: q.answered_at === null ? null : isoUtc(q.answered_at),
      stats: questionStats(q, readings, baseline, now),
    }))

  return {
    session: {
      id: session.id,
      user_name: session.user_name,
      started_at: isoUtc(session.started_at ?? now),
      ended_at: session.ended_at === null ? null : isoUtc(session.ended_at),
      baseline: baseline
        ? { heart_rate: baseline.heart_rate, breathing_rate: baseline.breathing_rate, focus: baseline.focus }
        : null,
    },
    series: buildSeries(readings),
    questions,
    coach_flags: flags.map((f) => ({ ts: f.ts, level: f.level, message: f.message })),
  }
}
