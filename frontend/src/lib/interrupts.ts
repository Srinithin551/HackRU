/**
 * Interrupt selection: the pure rules behind the gold box on the camera (see
 * components/GodInterrupt.tsx for the UI and the full description of the three rules).
 *
 * Kept separate from the component so it can be unit-tested without a renderer, the way the rest
 * of this codebase tests its logic.
 */
import type { CoachFlagEvent, QuestionEvent } from '../types.ts'

export type InterruptKind = 'rising' | 'high' | 'coaching'

export interface Interrupt {
  /** Unique per appearance, so React remounts (and replays the animation) for a new one. */
  key: string
  kind: InterruptKind
  /** Which god broke in. */
  god: string
  /** What they observed, citing the number that triggered it. */
  message: string
  /** The single thing to do, rendered after the word "Please". */
  command: string
}

/** Coaching has no command of its own: this is what complying with it means. */
const COACHING_COMMAND = 'answer the question that was actually asked'

/** The fallback command for a flag that arrived without one (older backend, fixtures). */
const DEFAULT_COMMAND = 'take one slow breath before you continue'

/**
 * Rule 2, as a pure function: the first of these flags whose text hasn't been shown yet.
 *
 * Returns null when every flag is a repeat — a repeat is swallowed, never displayed. Mutates
 * `shown` with whatever it picks, so the caller's set stays the session's record.
 */
export function nextInterrupt(
  fresh: readonly CoachFlagEvent[],
  shown: Set<string>,
  god: string,
): Interrupt | null {
  for (const flag of fresh) {
    const message = flag.message?.trim()
    if (!message || shown.has(message)) continue
    shown.add(message)
    return {
      key: `${flag.ts}:${message}`,
      kind: flag.level,
      god,
      message,
      command: flag.command?.trim() || DEFAULT_COMMAND,
    }
  }
  return null
}

/** The coaching attached to a question, once, if it hasn't been shown before. */
export function coachingInterrupt(
  question: QuestionEvent | null,
  shown: Set<string>,
  god: string,
): Interrupt | null {
  const message = question?.coaching?.trim()
  if (!message || shown.has(message)) return null
  shown.add(message)
  return { key: `coaching:${question?.question_id ?? ''}`, kind: 'coaching', god, message, command: COACHING_COMMAND }
}
