/**
 * Deterministic stand-in for Brain's gemini.final_report (brain.md). Same input,
 * same report, and every body_signal cites real numbers from the replay.
 *
 * composure_score = clamp(round(100 - 0.5 * avg stress_mean - 0.3 * max stress_peak
 *                                   - 3 * high flags - 1 * rising flag), 0, 100)
 * over the non-calibration answers that have vitals. A calm session scores ~90,
 * one hard spike lands in the 55 to 70 range.
 *
 * DOM-free.
 */
import type { CoachFlagEvent, ReplayQuestion, ReplayResponse, Report } from '../types.ts'
import { clamp, maxOf, mean, round } from './sim.ts'

const HIGH = 70
const RISING = 40

const HESITATIONS = /\b(um+|uh+|i think|not sure|blanking|i guess|i do not know|i don't know)\b/gi

/** Composure from the answers' stress and the coach flags (formula in the file header). */
export function composureScore(questions: readonly ReplayQuestion[], flags: readonly CoachFlagEvent[]): number {
  const answers = withVitals(questions)
  const avgStress = mean(answers.map((q) => q.stats?.stress_mean)) ?? 0
  const maxPeak = maxOf(answers.map((q) => q.stats?.stress_peak)) ?? 0
  const high = flags.filter((f) => f.level === 'high').length
  const rising = flags.length - high
  return clamp(Math.round(100 - 0.5 * avgStress - 0.3 * maxPeak - 3 * high - rising), 0, 100)
}

/** The report for one finished session. */
export function buildReport(replay: ReplayResponse, flags: readonly CoachFlagEvent[] = replay.coach_flags ?? []): Report {
  const questions = replay.questions.filter((q) => q.kind !== 'calibration').sort((a, b) => a.idx - b.idx)
  const baseHr = replay.session.baseline?.heart_rate ?? null
  const score = composureScore(questions, flags)
  const hardest = hardestQuestion(questions)

  return {
    composure_score: score,
    summary: summary(questions, flags, score, hardest),
    hardest_moment: hardestMoment(hardest, baseHr),
    per_question: questions.map((q) => ({
      question_id: q.id,
      what_happened: whatHappened(q),
      body_signal: bodySignal(q),
      advice: advice(q, questions),
    })),
    top_tips: topTips(replay, questions, flags, hardest, score),
  }
}

// ---------------------------------------------------------------------------

function withVitals(questions: readonly ReplayQuestion[]): ReplayQuestion[] {
  return questions.filter((q) => q.kind !== 'calibration' && q.stats !== null && q.stats.n_samples > 0)
}

function hardestQuestion(questions: readonly ReplayQuestion[]): ReplayQuestion | null {
  let best: ReplayQuestion | null = null
  for (const q of withVitals(questions)) {
    const peak = q.stats?.stress_peak ?? null
    if (peak === null) continue
    if (!best || peak > (best.stats?.stress_peak ?? -1)) best = q
  }
  return best
}

const label = (q: ReplayQuestion) => `Q${q.idx}`

function shortText(text: string, words = 8): string {
  const parts = text.trim().split(/\s+/)
  const head = parts.slice(0, words).join(' ').replace(/[.,;:!?]+$/, '')
  return parts.length > words ? `${head}...` : head
}

const signed = (x: number) => `${x >= 0 ? '+' : ''}${round(x, 0)}`

function summary(
  questions: readonly ReplayQuestion[],
  flags: readonly CoachFlagEvent[],
  score: number,
  hardest: ReplayQuestion | null,
): string {
  const answers = withVitals(questions)
  if (answers.length === 0) {
    return `Composure ${score}/100, but no usable vitals were recorded during your answers. Check the camera framing and run another session for a full read.`
  }
  const avg = round(mean(answers.map((q) => q.stats?.stress_mean)) ?? 0, 0)
  const calm = answers.filter((q) => (q.stats?.stress_peak ?? 0) < RISING).length
  const followUps = questions.filter((q) => q.kind === 'follow_up').length
  const first = `Composure ${score}/100 over ${questions.length} questions${followUps ? `, including ${followUps} follow-up${followUps > 1 ? 's' : ''}` : ''}, with stress averaging ${avg} out of 100.`
  const peak = hardest?.stats?.stress_peak
  const second =
    hardest && peak !== null && peak !== undefined
      ? `Your biggest reaction came on ${label(hardest)}, where stress peaked at ${round(peak, 0)}.`
      : 'Stress never spiked on any single answer.'
  const third = `${calm} of ${answers.length} answers stayed fully in the calm zone (under ${RISING})${flags.length ? ` and the coach flagged you ${flags.length} time${flags.length > 1 ? 's' : ''}` : ''}.`
  return `${first} ${second} ${third}`
}

function hardestMoment(q: ReplayQuestion | null, baseHr: number | null): string {
  const s = q?.stats
  if (!q || !s || s.stress_peak === null) {
    return 'No single spike stood out: there was not enough usable vitals data to find one.'
  }
  const when = s.peak_offset_s !== null ? `${round(s.peak_offset_s, 0)}s into your answer` : 'during your answer'
  const hr =
    s.hr_peak !== null
      ? baseHr
        ? `your heart rate hit ${s.hr_peak} bpm, ${round(((s.hr_peak - baseHr) / baseHr) * 100, 0)}% over your ${baseHr} bpm baseline, and `
        : `your heart rate hit ${s.hr_peak} bpm and `
      : ''
  return `On ${label(q)} ("${shortText(q.text)}"), ${when}, ${hr}stress peaked at ${s.stress_peak}.`
}

function whatHappened(q: ReplayQuestion): string {
  const transcript = (q.transcript ?? '').trim()
  if (!transcript) return 'No answer was captured: the question timed out or the session ended before you finished.'
  const words = transcript.split(/\s+/).length
  const hesitations = transcript.match(HESITATIONS)?.length ?? 0
  const duration = q.stats ? ` over ${round(q.stats.duration_s, 0)}s` : ''
  const lead = q.kind === 'follow_up' ? 'On the follow-up you' : 'You'
  const tail =
    hesitations >= 2
      ? ` It had ${hesitations} hesitations ("um", "I think") before the main point.`
      : ' The structure was clear and got to the point.'
  return `${lead} answered in about ${words} words${duration}, opening with "${shortText(transcript)}".${tail}`
}

function bodySignal(q: ReplayQuestion): string {
  const s = q.stats
  if (!s || s.n_samples === 0) {
    const duration = s ? round(s.duration_s, 0) : 0
    return `No usable vitals for this answer: 0 readings above 0.5 confidence over ${duration}s.`
  }
  const parts: string[] = []
  if (s.hr_mean !== null) {
    const delta = s.hr_delta_pct !== null ? ` (${signed(s.hr_delta_pct)}% vs baseline)` : ''
    parts.push(`HR averaged ${s.hr_mean} bpm${delta}${s.hr_peak !== null ? ` and peaked at ${s.hr_peak}` : ''}`)
  }
  if (s.stress_peak !== null) {
    const when = s.peak_offset_s !== null ? ` ${round(s.peak_offset_s, 0)}s in` : ''
    parts.push(`stress averaged ${s.stress_mean ?? s.stress_peak} and peaked at ${s.stress_peak}${when}`)
  }
  if (parts.length === 0) parts.push(`${s.n_samples} readings over ${round(s.duration_s, 0)}s`)
  const text = parts.join('; ')
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`
}

function advice(q: ReplayQuestion, all: readonly ReplayQuestion[]): string {
  const s = q.stats
  if (!s || s.n_samples === 0) return 'Stay centered in the camera frame so the sensor can read you for the whole answer.'
  const peak = s.stress_peak ?? 0
  if (q.kind === 'follow_up') {
    const parent = [...all].reverse().find((p) => p.idx < q.idx && p.kind === 'main')
    const parentPeak = parent?.stats?.stress_peak ?? null
    if (parentPeak !== null && peak < parentPeak) {
      return `Breaking the problem into one small step dropped your stress peak from ${parentPeak} to ${peak}. Next time, take that first small step yourself before the interviewer has to offer it.`
    }
  }
  if (peak > HIGH) {
    return 'When a question feels hard, say your first step out loud ("I would start by...") before reaching for the full answer. It buys thinking time and keeps your pulse down.'
  }
  if (peak > RISING) return 'Stress climbed mid-answer. Plan one deliberate pause with a slow breath before your second point.'
  const words = (q.transcript ?? '').trim().split(/\s+/).filter(Boolean).length
  if (words < 40) return 'You were calm here. Add one specific example with a number so the answer lands harder.'
  return 'Calm and well paced. Reuse this structure (headline first, then one example) on the harder questions.'
}

function topTips(
  replay: ReplayResponse,
  questions: readonly ReplayQuestion[],
  flags: readonly CoachFlagEvent[],
  hardest: ReplayQuestion | null,
  score: number,
): string[] {
  const tips: string[] = []
  const peak = hardest?.stats?.stress_peak ?? null
  if (hardest && peak !== null && peak > HIGH) {
    tips.push(
      `On questions like ${label(hardest)} (${hardest.stats?.peak_offset_s ?? 0}s in, stress ${peak}), start with the simplest approach out loud, then improve it.`,
    )
  }
  const baseBr = replay.session.baseline?.breathing_rate ?? null
  const maxBr = maxOf(withVitals(questions).map((q) => q.stats?.br_mean))
  if (baseBr && maxBr !== null && maxBr > baseBr * 1.1) {
    tips.push(`Practice slow exhales between points: your breathing averaged up to ${maxBr}/min against a ${baseBr}/min baseline.`)
  }
  if (flags.length > 0) {
    tips.push(`The coach flagged you ${flags.length} time${flags.length > 1 ? 's' : ''}. When you feel your pulse rise, slow your speaking pace instead of speeding up.`)
  }
  tips.push(
    'Open each answer with a one-sentence headline, then give one concrete example.',
    'Do 60 seconds of slow breathing before an interview so you start close to your baseline.',
    `Run another session this week and try to beat a composure score of ${score}.`,
  )
  return tips.slice(0, 3)
}
