/**
 * Interrupt selection: the pure rules behind the gold box on the camera (see
 * components/InterruptBox.tsx for the UI and the full description of the rules).
 *
 * Kept separate from the component so it can be unit-tested without a renderer, the way the rest
 * of this codebase tests its logic.
 */
import { estimateSpeechMs } from '../mocks/audio.ts'
import type { CoachFlagEvent, IntegrityFlagEvent, QuestionEvent } from '../types.ts'

export type InterruptKind = 'rising' | 'high' | 'coaching' | 'cheating'

export interface Interrupt {
  /** Unique per appearance, so React remounts (and replays the animation) for a new one. */
  key: string
  kind: InterruptKind
  /** Who broke in (the interviewer's name). */
  speaker: string
  /** What they observed, citing the number that triggered it. */
  message: string
  /** The single thing to do, rendered after the word "Please". */
  command: string
  /**
   * True when the interviewer speaks it (coaching rides in the question's clip): the box then stays up
   * until that narration ends. Silent interrupts (coach and integrity flags) stay up for readingMs().
   */
  narrated: boolean
}

/** Coaching has no command of its own: this is what complying with it means. */
const COACHING_COMMAND = 'answer the question that was actually asked'

/** The fallback command for a flag that arrived without one (older backend, fixtures). */
const DEFAULT_COMMAND = 'take one slow breath before you continue'

/** Extra time on top of the reading time, so the last words aren't snatched away mid-read. */
const READ_SLACK_MS = 1_500

/**
 * How long the box needs to stay up to be read: as long as narrating its text would take, plus a
 * little. A silent interrupt disappears after this; a narrated one never before it (a clip that fails
 * instantly must not flash the box away).
 */
export function readingMs(interrupt: Interrupt): number {
  return estimateSpeechMs(`${interrupt.message} Please ${interrupt.command}.`) + READ_SLACK_MS
}

/**
 * Rule 2, as a pure function: the first of these flags whose text hasn't been shown yet.
 *
 * Returns null when every flag is a repeat — a repeat is swallowed, never displayed. Mutates
 * `shown` with whatever it picks, so the caller's set stays the session's record.
 */
export function nextInterrupt(
  fresh: readonly CoachFlagEvent[],
  shown: Set<string>,
  speaker: string,
): Interrupt | null {
  for (const flag of fresh) {
    const message = flag.message?.trim()
    if (!message || shown.has(message)) continue
    shown.add(message)
    return {
      key: `${flag.ts}:${message}`,
      kind: flag.level,
      speaker,
      message,
      command: flag.command?.trim() || DEFAULT_COMMAND,
      narrated: false,
    }
  }
  return null
}

/** "Suspected of cheating": the first unseen integrity flag, under the same never-repeat rule. */
export function integrityInterrupt(
  fresh: readonly IntegrityFlagEvent[],
  shown: Set<string>,
  speaker: string,
): Interrupt | null {
  for (const flag of fresh) {
    const message = flag.message?.trim()
    if (!message || shown.has(message)) continue
    shown.add(message)
    return {
      key: `integrity:${flag.ts}`,
      kind: 'cheating',
      speaker,
      message,
      command: flag.command?.trim() || 'keep your eyes on the screen',
      narrated: false,
    }
  }
  return null
}

/** The coaching attached to a question, once, if it hasn't been shown before. */
export function coachingInterrupt(
  question: QuestionEvent | null,
  shown: Set<string>,
  speaker: string,
): Interrupt | null {
  const message = question?.coaching?.trim()
  if (!message || shown.has(message)) return null
  shown.add(message)
  return {
    key: `coaching:${question?.question_id ?? ''}`,
    kind: 'coaching',
    speaker,
    message,
    command: COACHING_COMMAND,
    // Spoken first in the question's clip; with no clip (TTS down) it's only read.
    narrated: question?.audio_url != null,
  }
}
