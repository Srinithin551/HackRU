/**
 * Panel 1 of the interview: the interviewer (Judge, one voice for the whole interview) and the question
 * being asked.
 *
 * Animation is deliberately limited, so the panel feels still rather than busy. What moves: Judge's aura
 * breathes, a gold halo pulses while Judge is actually speaking, and every new question gets one
 * transition: the portrait flares, a banner names the question ("Question 2 of 5"), and the question
 * text descends into place.
 *
 * The transition is staged entirely in CSS, keyed on the question id: no timers, so nothing can leave the
 * panel stuck mid-transition, and the banner's keyframe ends at opacity 0 so it clears itself.
 *
 * Interrupts do NOT appear here. They belong on the camera, against the candidate's own face
 * (see InterruptBox).
 */
import type { ReactNode } from 'react'
import { INTERVIEWER_NAME } from '../lib/constants.ts'
import type { QuestionEvent } from '../types.ts'

/** A pale statue bust. `speaking` adds the gold ring pulse. */
export function InterviewerBust({ speaking = false, size = 'sm' }: { speaking?: boolean; size?: 'sm' | 'lg' }) {
  const box = size === 'lg' ? 'size-14' : 'size-9'
  return (
    <span
      className={`relative grid shrink-0 place-items-center rounded-full border border-accent bg-pale-subtle transition-all duration-500 ${box} ${
        speaking ? 'animate-halo' : ''
      }`}
      title={INTERVIEWER_NAME}
    >
      <span className="absolute inset-0 animate-aura rounded-full bg-pale blur-md" aria-hidden="true" />
      <svg viewBox="0 0 32 40" className={`relative ${size === 'lg' ? 'h-10' : 'h-6'}`} aria-hidden="true">
        <circle cx="16" cy="12" r="7" className="fill-pale-deep" />
        <path d="M16 21c-6.5 0-10 4.5-11 13h22c-1-8.5-4.5-13-11-13Z" className="fill-pale-deep" />
      </svg>
    </span>
  )
}

function counterLabel(q: QuestionEvent | null): string {
  if (!q) return 'Getting ready'
  if (q.kind === 'calibration') return 'Before we begin'
  return `Question ${q.index} of ${q.total}`
}

/** What the transition banner says for a new question; null for the warm-up (nothing to announce yet). */
function bannerText(q: QuestionEvent | null): string | null {
  if (!q || q.kind === 'calibration') return null
  if (q.kind === 'follow_up') return `Pressing further on question ${q.index}`
  return `Question ${q.index} of ${q.total}`
}

export function InterviewerPanel({
  question,
  speaking,
  thinking,
  asked,
  children,
}: {
  question: QuestionEvent | null
  speaking: boolean
  thinking: boolean
  asked: readonly QuestionEvent[]
  /** Answer controls, supplied by the page (they own the mic and the interrupt gate). */
  children: ReactNode
}) {
  const earlier = asked.filter((a) => a.question_id !== question?.question_id)
  const banner = bannerText(question)
  // Keyed on the question, so the arrival replays once per new question (and not on re-renders).
  const transitionKey = question?.question_id ?? 'waiting'

  return (
    <aside className="panel-gold relative flex min-h-0 flex-col gap-4 p-panel">
      <div className="relative flex items-center gap-3">
        <span key={`bust:${transitionKey}`} className={banner ? 'animate-arrive' : ''}>
          <InterviewerBust speaking={speaking} size="lg" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-lead font-semibold tracking-tight">{INTERVIEWER_NAME}</p>
          <p className="label-caps">AI interviewer</p>
        </div>
        {thinking && !speaking && (
          <span className="flex items-end gap-1" aria-label="considering">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="size-1.5 animate-thinking rounded-full bg-accent"
                style={{ animationDelay: `${i * 0.16}s` }}
              />
            ))}
          </span>
        )}

        {/* Names the new question, then clears itself (the keyframe ends at opacity 0). */}
        {banner && (
          <p
            key={`banner:${transitionKey}`}
            className="animate-handover pointer-events-none absolute -top-1 left-0 right-0 rounded-control border border-accent/40 bg-surface-2/95 px-3 py-1.5 text-center font-mono text-micro uppercase tracking-[0.08em] text-accent backdrop-blur"
            aria-live="polite"
          >
            {banner}
          </p>
        )}
      </div>

      {/* The question. Scrolls before the answer controls do, so they never leave the screen. */}
      <div className="min-h-0 overflow-y-auto border-t border-hairline pt-4">
        <div className="flex items-center justify-between gap-2">
          <span className="label-caps">{counterLabel(question)}</span>
          {question?.kind === 'follow_up' && (
            <span className="rounded-full bg-accent-subtle px-2 py-0.5 font-mono text-micro uppercase text-accent">
              Pressing further
            </span>
          )}
        </div>

        {question && question.kind !== 'calibration' && question.total > 0 && (
          <div className="mt-2.5 flex gap-1.5" aria-hidden="true">
            {Array.from({ length: question.total }, (_, i) => (
              <span
                key={i}
                className={`h-1 flex-1 rounded-full transition-colors duration-500 ${
                  i + 1 < question.index ? 'bg-accent/50' : i + 1 === question.index ? 'bg-accent' : 'bg-surface-3'
                }`}
              />
            ))}
          </div>
        )}

        <p
          key={question?.question_id ?? 'waiting'}
          className="mt-3 animate-descend text-question font-medium tracking-tight text-balance"
        >
          {question ? question.text : `${INTERVIEWER_NAME} is getting ready…`}
        </p>
      </div>

      <div className="flex shrink-0 flex-col gap-2.5">{children}</div>

      {earlier.length > 0 && (
        <div className="mt-auto min-h-0 shrink-[100] overflow-y-auto border-t border-hairline pt-4">
          <p className="label-caps">Already asked</p>
          <ol className="mt-2 flex flex-col gap-2">
            {earlier.map((a) => (
              <li key={a.question_id} className="flex gap-2 text-caption text-ink-muted">
                <span className="shrink-0 font-mono text-ink-secondary">
                  {a.kind === 'calibration' ? '—' : `Q${a.index}`}
                </span>
                <span className="line-clamp-2">{a.text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </aside>
  )
}
