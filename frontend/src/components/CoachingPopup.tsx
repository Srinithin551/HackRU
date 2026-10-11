/**
 * Coaching that the interviewer says outside the interview frame ("Let me step out of the interview for
 * a second…"), shown on the interviewer panel.
 *
 * - CoachingPopup: pops up over the panel with the full text while the interviewer is speaking it (the
 *   user can't answer then anyway). "Got it" or Escape closes it.
 * - CoachingChip: the docked form once it's the user's turn, under the question. Click to reopen.
 */
import { useEffect } from 'react'
import { INTERVIEWER_NAME } from '../lib/constants.ts'

function StepOutIcon({ className = '' }: { className?: string }) {
  // Two bars: the interview is paused while the coach talks.
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className={className}>
      <circle cx="8" cy="8" r="7.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <rect x="5.5" y="4.75" width="1.75" height="6.5" rx="0.6" fill="currentColor" />
      <rect x="8.75" y="4.75" width="1.75" height="6.5" rx="0.6" fill="currentColor" />
    </svg>
  )
}

export function CoachingPopup({ text, speaking, onClose }: { text: string; speaking: boolean; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-labelledby="coaching-title"
      className="absolute inset-x-3 top-[4.75rem] z-20 flex max-h-[calc(100%-6rem)] animate-toast-in flex-col rounded-panel border border-rising bg-rising-subtle/95 shadow-[0_18px_50px_rgb(0_0_0/0.55)] backdrop-blur-md"
    >
      <div className="flex items-start gap-3 border-b border-rising/30 px-4 pt-4 pb-3">
        <StepOutIcon className="mt-0.5 size-5 shrink-0 text-rising" />
        <div className="min-w-0 flex-1">
          <p id="coaching-title" className="text-body font-semibold text-ink">
            Stepping out of the interview
          </p>
          <p className="text-caption text-ink-secondary">{INTERVIEWER_NAME}&apos;s coaching on your last answer</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close coaching note"
          className="-mt-1 -mr-1 rounded-control px-2 py-1 text-lead leading-none text-ink-secondary hover:bg-surface-2 hover:text-ink"
        >
          ×
        </button>
      </div>

      <p className="min-h-0 overflow-y-auto px-4 py-3 text-body leading-relaxed text-ink">{text}</p>

      <div className="flex items-center justify-between gap-3 border-t border-rising/30 px-4 py-3">
        <span className="flex items-center gap-2 text-caption text-ink-secondary">
          {speaking ? (
            <>
              <span className="size-2 animate-pulse rounded-full bg-rising" />
              {INTERVIEWER_NAME} is speaking · your next question follows
            </>
          ) : (
            'Back in the interview: your question is below'
          )}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-control bg-rising px-3 py-1.5 text-caption font-semibold text-page hover:opacity-90"
        >
          Got it
        </button>
      </div>
    </div>
  )
}

export function CoachingChip({ text, onOpen }: { text: string; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="mt-3 flex w-full animate-fade-in items-center gap-2.5 rounded-control border border-rising/50 bg-rising-subtle px-3 py-2 text-left hover:border-rising"
    >
      <StepOutIcon className="size-4 shrink-0 text-rising" />
      <span className="min-w-0 flex-1">
        <span className="label-caps block text-rising">Coaching note</span>
        <span className="line-clamp-1 text-caption text-ink-secondary">{text}</span>
      </span>
      <span className="shrink-0 text-caption font-medium text-rising">Read</span>
    </button>
  )
}
