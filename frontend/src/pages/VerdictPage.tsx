/**
 * The judgement (/session/:id/verdict). The last screen of the trial.
 *
 * The mortal has heard the gods' record and now has two ways out of the palace:
 *
 *   "Let me stand again"  → the gods say TRY AGAIN, the doors reopen, and a new trial begins.
 *   "I'll come back later" → the gods dismiss them, the marble floor opens, and the mortal drops
 *                            out of the hall. The program finishes there.
 *
 * The fall is the one big animation in the app, so it respects prefers-reduced-motion through the
 * global rule in tokens.css (the mortal simply fades instead).
 */
import { useEffect, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { getReport } from '../api.ts'
import { GodBust } from '../components/GodsPanel.tsx'
import { Brand, Button, GoldRule } from '../components/ui.tsx'
import { PANTHEON, PRESIDING_GOD } from '../lib/constants.ts'
import { stressColor } from '../styles/tokens.ts'
import type { Report } from '../types.ts'

/** How long the mortal falls before the palace closes (matches --animate-fall). */
const FALL_MS = 1_900
/**
 * Standing again is staged in two beats: the gods' ruling lands, then the doors reopen and the
 * gold swallows the screen so the new trial starts from inside the light rather than from a cut.
 * RETRY_HOLD_MS + the aperture's own 1100ms = RETRY_MS, so navigation happens exactly as the
 * screen finishes filling.
 */
const RETRY_HOLD_MS = 700
const RETRY_MS = RETRY_HOLD_MS + 1_100

type Stage = 'choosing' | 'retry' | 'falling' | 'closed'

export function VerdictPage() {
  const { id } = useParams()
  if (!id) return <Navigate to="/" replace />
  return <Verdict key={id} sessionId={id} />
}

function judgement(score: number | null): { line: string; sub: string } {
  if (score === null) return { line: 'The gods withhold their judgement.', sub: 'They could not read you clearly enough to rule.' }
  if (score >= 80) return { line: 'You held your ground.', sub: 'The hall did not move you. Few mortals manage it.' }
  if (score >= 60) return { line: 'You stood, mostly.', sub: 'You wavered, but you did not break. That is worth something.' }
  if (score >= 40) return { line: 'The hall got the better of you.', sub: 'Your words held while your body gave you away.' }
  return { line: 'You came apart before us.', sub: 'There is no shame in it. There is only practice.' }
}

function Verdict({ sessionId }: { sessionId: string }) {
  const { search } = useLocation() // keeps ?mock=auto
  const navigate = useNavigate()
  const [report, setReport] = useState<Report | null>(null)
  const [stage, setStage] = useState<Stage>('choosing')

  useEffect(() => {
    let live = true
    getReport(sessionId)
      .then((r) => {
        if (live) setReport(r)
      })
      .catch(() => {
        /* the judgement still works without the number */
      })
    return () => {
      live = false
    }
  }, [sessionId])

  // The floor has opened: let the mortal fall, then close the palace for good.
  useEffect(() => {
    if (stage !== 'falling') return
    const timer = setTimeout(() => setStage('closed'), FALL_MS)
    return () => clearTimeout(timer)
  }, [stage])

  // "Then try again." is spoken by the hall before the doors reopen, so the ruling lands before
  // the mortal is sent back to the gates.
  useEffect(() => {
    if (stage !== 'retry') return
    const timer = setTimeout(() => navigate({ pathname: '/', search }), RETRY_MS)
    return () => clearTimeout(timer)
  }, [stage, navigate, search])

  const score = report?.composure_measured === false ? null : (report?.composure_score ?? null)
  const { line, sub } = judgement(score)

  if (stage === 'closed') return <PalaceClosed search={search} />

  return (
    <div className="bg-monitor relative flex min-h-dvh flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between px-gutter">
        <Brand />
        <Link to={{ pathname: `/session/${sessionId}/report`, search }} className="text-caption text-ink-secondary hover:text-ink">
          ← Back to the record
        </Link>
      </header>

      <main className="relative flex flex-1 flex-col items-center justify-center gap-8 p-gutter text-center">
        {/* The bench, assembled to deliver the ruling. */}
        <div className="flex items-end gap-3" aria-hidden="true">
          {[PRESIDING_GOD, ...PANTHEON].map((g, i) => (
            <div key={g.name} style={{ animationDelay: `${i * 0.12}s` }} className="animate-descend">
              <GodBust god={g} active size={i === 0 ? 'lg' : 'sm'} />
            </div>
          ))}
        </div>

        <div className="max-w-2xl">
          <p className="label-caps">The judgement of the hall</p>
          <h1 className="mt-3 text-[clamp(2rem,1.2rem+2.4vw,3.5rem)] leading-[1.05] font-semibold tracking-tight text-balance">
            {stage === 'retry' ? 'Then try again.' : stage === 'falling' ? 'Come back later.' : line}
          </h1>
          <p className="mt-4 text-lead text-ink-secondary">
            {stage === 'retry'
              ? 'The doors are opening. Take your place once more.'
              : stage === 'falling'
                ? 'The floor opens. The hall is done with you for today.'
                : sub}
          </p>
          {score !== null && stage === 'choosing' && (
            <p className="mt-6 inline-flex items-baseline gap-2 rounded-full border border-hairline bg-surface-1 px-4 py-2">
              <span className="label-caps">Composure</span>
              <span className="text-title font-semibold tabular-nums" style={{ color: stressColor(100 - score) }}>
                {score}
              </span>
              <span className="text-caption text-ink-muted">/ 100</span>
            </p>
          )}
        </div>

        <GoldRule className="max-w-sm" />

        {stage === 'choosing' && (
          <div className="flex flex-col items-center gap-3 sm:flex-row">
            <Button className="px-6 py-3 text-lead" onClick={() => setStage('retry')}>
              Let me stand again
            </Button>
            <Button variant="secondary" className="px-6 py-3 text-lead" onClick={() => setStage('falling')}>
              I&apos;ll come back later
            </Button>
          </div>
        )}

        {/* The mortal: still while judged, dropping once dismissed. */}
        <Mortal falling={stage === 'falling'} />

        {stage === 'falling' && <FloorCrack />}
        {stage === 'retry' && <DoorsReopening />}
      </main>

      <footer className="px-gutter pb-4 text-center font-mono text-micro text-ink-muted">
        Vitals by Presage · Stored in Tiger Data · Interviewer by Gemini + ElevenLabs
      </footer>
    </div>
  )
}

/** The human standing before the bench. */
function Mortal({ falling }: { falling: boolean }) {
  return (
    <div className={`relative z-10 ${falling ? 'animate-fall' : 'animate-rise'}`} aria-hidden="true">
      <svg viewBox="0 0 40 56" className="h-20">
        <circle cx="20" cy="11" r="8" className="fill-ink-secondary" />
        <path d="M20 21c-8 0-13 6-14 19 0 0 6 4 14 4s14-4 14-4c-1-13-6-19-14-19Z" className="fill-ink-secondary" />
      </svg>
    </div>
  )
}

/**
 * The doors reopening: a gold aperture widening out of the centre until it takes the whole hall,
 * handing the mortal straight into the next trial. Held back by RETRY_HOLD_MS so the gods' ruling
 * is read first.
 */
function DoorsReopening() {
  return (
    <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center overflow-hidden" aria-hidden="true">
      <div
        className="animate-aperture size-40 rounded-full bg-[radial-gradient(circle,rgb(255_243_196)_0%,rgb(212_175_55)_55%,rgb(176_143_38)_100%)] shadow-[0_0_120px_40px_rgb(212_175_55/0.6)]"
        style={{ animationDelay: `${RETRY_HOLD_MS}ms` }}
      />
    </div>
  )
}

/** The marble floor opening under the dismissed. */
function FloorCrack() {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-24 flex justify-center" aria-hidden="true">
      <div className="h-6 w-72 animate-fade-in rounded-[50%] bg-page shadow-[0_0_0_2px_rgb(212_175_55/0.45),0_0_70px_26px_rgb(0_0_0/0.9)]" />
    </div>
  )
}

/** After the fall: the palace is closed and the program is finished. */
function PalaceClosed({ search }: { search: string }) {
  return (
    <div className="bg-monitor grid min-h-dvh animate-fade-in place-items-center p-gutter text-center">
      <div className="flex max-w-lg flex-col items-center gap-5">
        {/* Shut doors: two marble leaves with a gold seam. */}
        <div className="flex h-28 w-40 overflow-hidden rounded-t-full border border-border-strong" aria-hidden="true">
          <div className="column-flute h-full w-1/2 border-r border-accent/40" />
          <div className="column-flute h-full w-1/2" />
        </div>
        <h1 className="text-title font-semibold">The palace has closed.</h1>
        <p className="text-body text-ink-secondary">
          The gods have returned to their own business. Come back when you are ready to stand again.
        </p>
        <Link to={{ pathname: '/', search }}>
          <Button variant="secondary">Knock again</Button>
        </Link>
      </div>
    </div>
  )
}
