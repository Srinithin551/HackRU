/**
 * Interrupts: the moment Judge breaks in mid-answer.
 *
 * Three things interrupt the candidate: a coach flag (the body betrayed them: heart rate, breathing,
 * composure), coaching (the answer itself missed the question) and an integrity flag (they kept
 * looking away from the screen: "I suspect you of cheating"). All land in the same box in the
 * top-right of the camera, never in the question panel, so the candidate sees it against their own face.
 *
 * Three rules the trial depends on:
 *  1. RESET. An interrupt belongs to the question it was raised on. When the trial moves to the next
 *     question the box clears, so a stale correction can never hang over a new answer.
 *  2. NEVER REPEAT. The same words twice read as a bug, not an interviewer. The backend rotates its phrasings
 *     (brain/watcher.py VARIANTS, brain/integrity.py VARIANTS) and `useInterrupt` drops any text already
 *     shown this session, so a repeat is swallowed rather than displayed.
 *  3. GOES AWAY ON ITS OWN. Nothing to click. Coaching is spoken (it opens the question's clip), so its
 *     box leaves when that narration ends; silent flags leave once they have had time to be read.
 */
import type { Interrupt, InterruptKind } from '../lib/interrupts.ts'

/** `label` follows the speaker's name: "Judge notices". */
const KIND_STYLE: Record<InterruptKind, { ring: string; chip: string; label: string; glow: string }> = {
  rising: { ring: 'border-rising bg-rising-subtle', chip: 'bg-rising text-on-accent', label: 'notices',
    glow: 'shadow-[0_0_40px_-6px_rgb(245_184_61/0.55)]' },
  high: { ring: 'border-high bg-high-subtle', chip: 'bg-high text-on-accent', label: 'interrupts',
    glow: 'shadow-[0_0_40px_-6px_rgb(240_82_74/0.6)]' },
  coaching: { ring: 'border-gold bg-gold-subtle', chip: 'bg-gold text-on-accent', label: 'corrects you',
    glow: 'shadow-[0_0_40px_-6px_rgb(212_175_55/0.55)]' },
  cheating: { ring: 'border-high bg-high-subtle', chip: 'bg-high text-on-accent', label: 'suspects cheating',
    glow: 'shadow-[0_0_48px_-4px_rgb(240_82_74/0.75)]' },
}

/** The box in the camera's top-right. Informational only: it removes itself (see useInterrupt). */
export function InterruptBox({ interrupt }: { interrupt: Interrupt }) {
  const style = KIND_STYLE[interrupt.kind]
  return (
    <div
      role="alert"
      aria-live="assertive"
      aria-label={`${interrupt.speaker} ${style.label}`}
      className={`w-[min(22rem,calc(100vw-3rem))] animate-toast-in rounded-panel border-2 ${style.ring} p-3.5 ${style.glow} backdrop-blur-md`}
    >
      <div className="flex items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 font-mono text-micro uppercase tracking-[0.08em] ${style.chip}`}>
          {interrupt.speaker} {style.label}
        </span>
      </div>

      <p className="mt-2 text-body font-medium text-ink">{interrupt.message}</p>

      <p className="mt-2 border-t border-ink/10 pt-2 text-body text-ink">
        <span className="font-semibold">Please {interrupt.command}.</span>
      </p>
    </div>
  )
}
