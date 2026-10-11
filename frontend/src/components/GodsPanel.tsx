/**
 * Panel 1 of the trial: the gods, and the question the current one is putting to the mortal.
 *
 * Animation is deliberately limited — the palace should feel still and heavy, not busy. What moves:
 * the active god's aura breathes, a gold halo pulses while that god is actually speaking, a new
 * question descends into place, and — the one real set piece — the floor passing from one god to
 * the next between questions.
 *
 * The handover is staged entirely in CSS, keyed on the god's name: the outgoing bust steps back
 * into the dark, the incoming one descends and flares gold, and a banner names the exchange before
 * clearing itself. No timers, so nothing can leave the panel stuck mid-handover.
 *
 * Interrupts do NOT appear here. They belong on the camera, against the mortal's own face
 * (see GodInterrupt).
 */
import type { ReactNode } from 'react'
import { godForQuestion, PANTHEON, PRESIDING_GOD, type God } from '../lib/constants.ts'
import type { QuestionEvent } from '../types.ts'

/** Which god a question belongs to. Calibration is always the presiding god. */
const godOf = (q: QuestionEvent): God => godForQuestion(q.kind === 'calibration' ? 0 : q.index)

/** A pale statue bust. `speaking` adds the gold ring pulse; `active` lifts it out of the row. */
export function GodBust({
  god,
  active = false,
  speaking = false,
  size = 'sm',
}: {
  god: God
  active?: boolean
  speaking?: boolean
  size?: 'sm' | 'lg'
}) {
  const box = size === 'lg' ? 'size-14' : 'size-9'
  return (
    <span
      className={`relative grid shrink-0 place-items-center rounded-full border transition-all duration-500 ${box} ${
        active ? 'border-accent bg-pale-subtle' : 'border-hairline bg-surface-2 opacity-55'
      } ${speaking ? 'animate-halo' : ''}`}
      title={`${god.name} · ${god.domain}`}
    >
      {active && <span className="absolute inset-0 animate-aura rounded-full bg-pale blur-md" aria-hidden="true" />}
      <svg viewBox="0 0 32 40" className={`relative ${size === 'lg' ? 'h-10' : 'h-6'}`} aria-hidden="true">
        <circle cx="16" cy="12" r="7" className={active ? 'fill-pale-deep' : 'fill-pale'} />
        <path d="M16 21c-6.5 0-10 4.5-11 13h22c-1-8.5-4.5-13-11-13Z" className={active ? 'fill-pale-deep' : 'fill-pale'} />
      </svg>
    </span>
  )
}

function counterLabel(q: QuestionEvent | null): string {
  if (!q) return 'The hall is gathering'
  if (q.kind === 'calibration') return 'Before the trial'
  return `Question ${q.index} of ${q.total}`
}

export function GodsPanel({
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
  const god = godForQuestion(question?.kind === 'calibration' ? 0 : (question?.index ?? 0))
  const earlier = asked.filter((a) => a.question_id !== question?.question_id)

  // The floor passing from one god to the next, derived from the questions already asked rather
  // than from render history, so it survives a reconnect replaying the list. `handing` stays true
  // for the whole question, which is harmless: every handover animation is keyed on the god's name
  // and one-shot, and the banner's keyframe ends at opacity 0 so it clears itself.
  const previousQuestion = asked.length > 1 ? asked[asked.length - 2] : null
  const previousName = previousQuestion ? godOf(previousQuestion).name : undefined
  const handing = previousName !== undefined && previousName !== god.name

  return (
    <aside className="panel-gold relative flex min-h-0 flex-col gap-4 p-panel">
      {/* The bench: every god in the hall, the one questioning you lit. */}
      <div className="flex items-center gap-1.5">
        {[PRESIDING_GOD, ...PANTHEON].map((g) => {
          const isActive = g.name === god.name
          return (
            <span key={g.name} className={handing && isActive ? 'animate-god-arrive' : ''}>
              <GodBust god={g} active={isActive} speaking={speaking && isActive} />
            </span>
          )
        })}
      </div>

      <div className="relative flex items-center gap-3 border-t border-hairline pt-4">
        {/* Keyed on the name so the incoming god's card replays its arrival. */}
        <span key={`bust:${god.name}`} className={handing ? 'animate-god-arrive' : ''}>
          <GodBust god={god} active speaking={speaking} size="lg" />
        </span>
        <div key={`name:${god.name}`} className={`min-w-0 flex-1 ${handing ? 'animate-god-arrive' : ''}`}>
          <p className="text-lead font-semibold tracking-tight">{god.name}</p>
          <p className="label-caps">{god.domain}</p>
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

        {/* Names the exchange, then clears itself (the keyframe ends at opacity 0). */}
        {handing && (
          <p
            key={`handover:${god.name}`}
            className="animate-handover pointer-events-none absolute -top-1 left-0 right-0 rounded-control border border-accent/40 bg-surface-2/95 px-3 py-1.5 text-center font-mono text-micro uppercase tracking-[0.08em] text-accent backdrop-blur"
            aria-live="polite"
          >
            {previousName} yields · {god.name} takes the floor
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
                className={`h-1 flex-1 rounded-full ${
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
          {question ? question.text : 'The gods are taking their seats…'}
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
