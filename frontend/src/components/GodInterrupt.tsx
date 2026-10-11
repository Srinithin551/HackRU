/**
 * Interrupts: the moment a god breaks in mid-answer.
 *
 * Two things interrupt a mortal — a coach flag (the body betrayed them: heart rate, breathing,
 * composure) and coaching (the answer itself missed the question). Both land in the same gold box
 * in the top-right of the camera, never in the question panel, so the mortal sees the correction
 * against their own face.
 *
 * Three rules the trial depends on:
 *  1. RESET. An interrupt belongs to the question it was raised on. When the trial moves to the next
 *     question the box clears, so a stale correction can never hang over a new answer.
 *  2. NEVER REPEAT. The same words twice read as a bug, not a god. The backend rotates its phrasings
 *     (brain/watcher.py VARIANTS) and `useInterrupt` drops any text already shown this session, so a
 *     repeat is swallowed rather than displayed.
 *  3. COMPLY FIRST. Every interrupt carries one command ("Please take one slow breath"). Until the
 *     mortal acknowledges it, the answer can't be submitted and the silence auto-stop is held off —
 *     the gods are owed an answer to the correction before the trial moves on.
 */
import type { Interrupt, InterruptKind } from '../lib/interrupts.ts'

const KIND_STYLE: Record<InterruptKind, { ring: string; chip: string; label: string; glow: string }> = {
  rising: { ring: 'border-rising bg-rising-subtle', chip: 'bg-rising text-on-accent', label: 'A god stirs',
    glow: 'shadow-[0_0_40px_-6px_rgb(245_184_61/0.55)]' },
  high: { ring: 'border-high bg-high-subtle', chip: 'bg-high text-on-accent', label: 'A god rises',
    glow: 'shadow-[0_0_40px_-6px_rgb(240_82_74/0.6)]' },
  coaching: { ring: 'border-gold bg-gold-subtle', chip: 'bg-gold text-on-accent', label: 'A god corrects you',
    glow: 'shadow-[0_0_40px_-6px_rgb(212_175_55/0.55)]' },
}

/**
 * The gold box in the camera's top-right. Not dismissible by clicking away: the mortal must press
 * the button, which is what "comply before moving on" means.
 */
export function GodInterrupt({ interrupt, onAcknowledge }: { interrupt: Interrupt; onAcknowledge: () => void }) {
  const style = KIND_STYLE[interrupt.kind]
  return (
    <div
      role="alertdialog"
      aria-live="assertive"
      aria-label={`${interrupt.god} interrupts`}
      className={`pointer-events-auto w-[min(22rem,calc(100vw-3rem))] animate-toast-in rounded-panel border-2 ${style.ring} p-3.5 ${style.glow} backdrop-blur-md`}
    >
      <div className="flex items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 font-mono text-micro uppercase tracking-[0.08em] ${style.chip}`}>
          {style.label}
        </span>
        <span className="truncate font-mono text-micro uppercase tracking-[0.08em] text-ink-secondary">
          {interrupt.god}
        </span>
      </div>

      <p className="mt-2 text-body font-medium text-ink">{interrupt.message}</p>

      {/* The command. This is the part that must be obeyed before the trial continues. */}
      <p className="mt-2 border-t border-ink/10 pt-2 text-body text-ink">
        <span className="font-semibold">Please {interrupt.command}.</span>
      </p>

      <button
        type="button"
        onClick={onAcknowledge}
        autoFocus
        className="mt-3 w-full rounded-control bg-accent px-3 py-2 text-caption font-semibold text-on-accent transition-colors hover:bg-accent-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        As you command
      </button>
    </div>
  )
}
