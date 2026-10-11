/**
 * Mock chart notes: the in-browser stand-in for GET /api/sessions/{id}/insights.
 *
 * Mirrors backend/brain/insights.py `fallback_notes` — the numbers-only path, which is what the
 * real backend serves whenever Gemini is unavailable. Enough to exercise the tooltips without a key.
 */
import type { ChartNote, ReplayResponse } from '../types.ts'

const n = (v: number | null | undefined, digits = 0): string =>
  typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : 'n/a'

const pct = (v: number | null | undefined): string =>
  typeof v === 'number' && Number.isFinite(v) ? `${v > 0 ? '+' : ''}${v.toFixed(0)}%` : 'n/a'

function trend(value: number | null | undefined, rising: number, high: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'no reading'
  if (value > high) return 'well above'
  if (value > rising) return 'above'
  return 'steady at'
}

export function buildInsights(replay: ReplayResponse): ChartNote[] {
  const notes: ChartNote[] = []
  for (const q of replay.questions) {
    const st = q.stats
    if (!st || st.n_samples === 0 || q.kind === 'calibration') continue

    notes.push({
      question_id: q.id,
      variable: 'heart_rate',
      headline: `${n(st.hr_mean)} bpm average`,
      detail: `Your heart rate averaged ${n(st.hr_mean)} bpm here, ${trend(st.hr_delta_pct, 8, 20)} your baseline (${pct(st.hr_delta_pct)}), peaking at ${n(st.hr_peak)}.`,
    })

    if (typeof st.br_mean === 'number') {
      notes.push({
        question_id: q.id,
        variable: 'breathing',
        headline: `${n(st.br_mean)} breaths a minute`,
        detail: `You averaged ${n(st.br_mean)} breaths a minute while answering this one.`,
      })
    }

    if (typeof st.stress_mean === 'number') {
      const when = typeof st.peak_offset_s === 'number' ? ` about ${n(st.peak_offset_s)}s in` : ''
      notes.push({
        question_id: q.id,
        variable: 'composure',
        headline: `Peaked at ${n(st.stress_peak)}`,
        detail: `Stress averaged ${n(st.stress_mean)} and peaked at ${n(st.stress_peak)}${when}, on the 0-100 scale where over 70 is high.`,
      })
    }
  }
  return notes
}
