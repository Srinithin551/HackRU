/**
 * The interrupt Judge is currently showing (see components/InterruptBox.tsx for the UI and
 * lib/interrupts.ts for the selection rules).
 */
import { useEffect, useRef, useState } from 'react'
import { coachingInterrupt, integrityInterrupt, nextInterrupt, readingMs, type Interrupt } from '../lib/interrupts.ts'
import type { CoachFlagEvent, IntegrityFlagEvent, QuestionEvent } from '../types.ts'

interface Raised {
  interrupt: Interrupt
  forQuestion: string | null
  /** epoch ms when it appeared */
  shownAt: number
}

/**
 * The interrupt on screen now, or null. It removes itself: a narrated one when the interviewer stops
 * speaking (`speaking` turns false), a silent one once it has had time to be read. Nothing to click.
 */
export function useInterrupt(
  question: QuestionEvent | null,
  flags: readonly CoachFlagEvent[],
  integrityFlags: readonly IntegrityFlagEvent[],
  speaking: boolean,
  speaker: string,
): Interrupt | null {
  // An interrupt is stored with the question it was raised on. Rule 1 (reset on a new question) is
  // then a render-time comparison rather than a clearing effect, so a stale correction can never be
  // painted for even one frame against the next question.
  const [raised, setRaised] = useState<Raised | null>(null)
  // Every text shown this session. Rule 2: a phrasing is never displayed twice.
  const shownTexts = useRef(new Set<string>())
  // How many flags of each kind we have already turned into interrupts, so we only react to new ones.
  const handledFlags = useRef(0)
  const handledIntegrity = useRef(0)
  const questionId = question?.question_id ?? null

  // Coaching arrives attached to the question itself.
  useEffect(() => {
    const next = coachingInterrupt(question, shownTexts.current, speaker)
    if (next) setRaised({ interrupt: next, forQuestion: question?.question_id ?? null, shownAt: Date.now() })
  }, [question, speaker])

  // Coach flags arrive mid-answer. Only the newest unseen one is shown: by the time it is read the
  // others describe a body state that has already passed.
  useEffect(() => {
    if (flags.length <= handledFlags.current) {
      handledFlags.current = flags.length // the list only grows; shorter means a fresh session
      return
    }
    const fresh = flags.slice(handledFlags.current)
    handledFlags.current = flags.length
    const next = nextInterrupt(fresh, shownTexts.current, speaker)
    if (next) setRaised({ interrupt: next, forQuestion: questionId, shownAt: Date.now() })
  }, [flags, speaker, questionId])

  // "Suspected of cheating" takes the box over from whatever is showing.
  useEffect(() => {
    if (integrityFlags.length <= handledIntegrity.current) {
      handledIntegrity.current = integrityFlags.length
      return
    }
    const fresh = integrityFlags.slice(handledIntegrity.current)
    handledIntegrity.current = integrityFlags.length
    const next = integrityInterrupt(fresh, shownTexts.current, speaker)
    if (next) setRaised({ interrupt: next, forQuestion: questionId, shownAt: Date.now() })
  }, [integrityFlags, speaker, questionId])

  // Rule 3: it goes away on its own. A narrated interrupt waits for the narration to end; either kind
  // stays at least its reading time.
  useEffect(() => {
    if (!raised || (raised.interrupt.narrated && speaking)) return
    const wait = Math.max(0, raised.shownAt + readingMs(raised.interrupt) - Date.now())
    const timer = setTimeout(() => setRaised((current) => (current === raised ? null : current)), wait)
    return () => clearTimeout(timer)
  }, [raised, speaking])

  return raised && raised.forQuestion === questionId ? raised.interrupt : null
}
