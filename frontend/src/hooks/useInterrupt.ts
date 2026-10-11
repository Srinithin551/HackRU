/**
 * The interrupt Judge is currently owed a response to (see components/InterruptBox.tsx for the UI
 * and lib/interrupts.ts for the selection rules).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { coachingInterrupt, nextInterrupt, type Interrupt } from '../lib/interrupts.ts'
import type { CoachFlagEvent, QuestionEvent } from '../types.ts'

/**
 * The interrupt currently owed a response, plus `acknowledge` to clear it.
 *
 * `pending` is true whenever the candidate still owes Judge compliance; the live page uses it to
 * gate the "I'm done" button and the speech auto-stop.
 */
export function useInterrupt(
  question: QuestionEvent | null,
  flags: readonly CoachFlagEvent[],
  speaker: string,
): { interrupt: Interrupt | null; pending: boolean; acknowledge: () => void } {
  // An interrupt is stored with the question it was raised on. Rule 1 (reset on a new question) is
  // then a render-time comparison rather than a clearing effect, so a stale correction can never be
  // painted for even one frame against the next question.
  const [raised, setRaised] = useState<{ interrupt: Interrupt; forQuestion: string | null } | null>(null)
  // Every text shown this session. Rule 2: a phrasing is never displayed twice.
  const shownTexts = useRef(new Set<string>())
  // How many flags we have already turned into interrupts, so we only react to genuinely new ones.
  const handledFlags = useRef(0)
  const questionId = question?.question_id ?? null

  // Coaching arrives attached to the question itself.
  useEffect(() => {
    const next = coachingInterrupt(question, shownTexts.current, speaker)
    if (next) setRaised({ interrupt: next, forQuestion: question?.question_id ?? null })
  }, [question, speaker])

  // Coach flags arrive mid-answer. Only the newest unseen one is shown: by the time it has been
  // acknowledged the others describe a body state that has already passed.
  useEffect(() => {
    if (flags.length <= handledFlags.current) {
      handledFlags.current = flags.length // the list only grows; shorter means a fresh session
      return
    }
    const fresh = flags.slice(handledFlags.current)
    handledFlags.current = flags.length
    const next = nextInterrupt(fresh, shownTexts.current, speaker)
    if (next) setRaised({ interrupt: next, forQuestion: questionId })
  }, [flags, speaker, questionId])

  const acknowledge = useCallback(() => setRaised(null), [])
  const interrupt = raised && raised.forQuestion === questionId ? raised.interrupt : null
  return { interrupt, pending: interrupt !== null, acknowledge }
}
