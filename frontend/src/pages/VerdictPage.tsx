/**
 * The judgement (/session/:id/verdict). The last screen of the interview.
 *
 * Judge's ruling arrives in a short stagger (portrait, ruling, score counting up, choices), then the
 * candidate has two ways out:
 *
 *   "Take the stand again"  → the ruling changes to "Then try again.", and a gold light opens out from the
 *                             centre until it fills the screen; the new interview starts from inside it.
 *   "I'll come back later"  → the page sinks and fades while the screen dims, and court is adjourned.
 *
 * Both exits are a single clip-path / opacity animation each (crisp at any size, no scaled-up shapes),
 * and both respect prefers-reduced-motion through the global rule in tokens.css (they simply cut).
 */
import { useEffect, useState, type CSSProperties } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { getReport } from '../api.ts'
import { InterviewerBust } from '../components/InterviewerPanel.tsx'
import { Brand, Button, GoldRule } from '../components/ui.tsx'
import { useTweenedNumber } from '../hooks/useTweenedNumber.ts'
import { INTERVIEWER_NAME } from '../lib/constants.ts'
import { stressColor } from '../styles/tokens.ts'
import type { Report } from '../types.ts'

/** "Then try again." is read before the light opens; then the light takes --animate-iris-open (950 ms). */
const RETRY_HOLD_MS = 550
const RETRY_MS = RETRY_HOLD_MS + 950
/** How long the page takes to sink and dim before the closed screen (matches --animate-recede). */
const LEAVE_MS = 900

type Stage = 'choosing' | 'retry' | 'leaving' | 'closed'

export function VerdictPage() {
  const { id } = useParams()
  if (!id) return <Navigate to="/" replace />
  return <Verdict key={id} sessionId={id} />
}

function judgement(score: number | null): { line: string; sub: string } {
  if (score === null) return { line: `${INTERVIEWER_NAME} withholds judgement.`, sub: `${INTERVIEWER_NAME} could not read you clearly enough to rule.` }
  if (score >= 80) return { line: 'You held your ground.', sub: 'The pressure did not move you. Few who take the stand manage it.' }
  if (score >= 60) return { line: 'You stood, mostly.', sub: 'You wavered, but you did not break. That is worth something.' }
  if (score >= 40) return { line: 'The pressure got the better of you.', sub: 'Your words held while your body gave you away.' }
  return { line: 'You came apart before me.', sub: 'There is no shame in it. There is only practice.' }
}

/** Staggered entrance: each piece rises in `step` after the previous one. */
const enter = (step: number): CSSProperties => ({ animationDelay: `${120 + step * 110}ms` })

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

  useEffect(() => {
    if (stage === 'retry') {
      const timer = setTimeout(() => navigate({ pathname: '/', search }), RETRY_MS)
      return () => clearTimeout(timer)
    }
    if (stage === 'leaving') {
      const timer = setTimeout(() => setStage('closed'), LEAVE_MS)
      return () => clearTimeout(timer)
    }
  }, [stage, navigate, search])

  const score = report?.composure_measured === false ? null : (report?.composure_score ?? null)
  const shownScore = useTweenedNumber(score)
  const { line, sub } = judgement(score)

  if (stage === 'closed') return <CourtAdjourned search={search} />

  return (
    <div className="bg-monitor relative flex min-h-dvh flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between px-gutter">
        <Brand />
        <Link to={{ pathname: `/session/${sessionId}/report`, search }} className="text-caption text-ink-secondary hover:text-ink">
          ← Back to the record
        </Link>
      </header>

      <main
        className={`relative flex flex-1 flex-col items-center justify-center gap-8 p-gutter text-center ${
          stage === 'leaving' ? 'animate-recede' : ''
        }`}
      >
        <div className="animate-descend" style={enter(0)} aria-hidden="true">
          <InterviewerBust size="lg" speaking={stage === 'choosing'} />
        </div>

        <div className="max-w-2xl">
          <p className="label-caps animate-rise" style={enter(1)}>
            The court&apos;s ruling
          </p>
          {/* Keyed on the stage, so a changed ruling ("Then try again.") rises in fresh. */}
          <h1
            key={`line:${stage}`}
            className="mt-3 animate-rise text-[clamp(2rem,1.2rem+2.4vw,3.5rem)] leading-[1.05] font-semibold tracking-tight text-balance"
            style={stage === 'choosing' ? enter(2) : undefined}
          >
            {stage === 'retry' ? 'Then try again.' : stage === 'leaving' ? 'Come back later.' : line}
          </h1>
          <p key={`sub:${stage}`} className="mt-4 animate-rise text-lead text-ink-secondary" style={stage === 'choosing' ? enter(3) : undefined}>
            {stage === 'retry'
              ? 'The court reconvenes. Take the stand once more.'
              : stage === 'leaving'
                ? 'Court is adjourned for today.'
                : sub}
          </p>
          {score !== null && stage === 'choosing' && (
            <p className="mt-6 inline-flex animate-rise items-baseline gap-2 rounded-full border border-hairline bg-surface-1 px-4 py-2" style={enter(4)}>
              <span className="label-caps">Composure</span>
              <span className="text-title font-semibold tabular-nums" style={{ color: stressColor(100 - score) }}>
                {Math.round(shownScore ?? score)}
              </span>
              <span className="text-caption text-ink-muted">/ 100</span>
            </p>
          )}
        </div>

        <GoldRule className="max-w-sm animate-fade-in" />

        {stage === 'choosing' && (
          <div className="flex animate-rise flex-col items-center gap-3 sm:flex-row" style={enter(5)}>
            <Button className="px-6 py-3 text-lead" onClick={() => setStage('retry')}>
              Take the stand again
            </Button>
            <Button variant="secondary" className="px-6 py-3 text-lead" onClick={() => setStage('leaving')}>
              I&apos;ll come back later
            </Button>
          </div>
        )}
      </main>

      <footer className="px-gutter pb-4 text-center font-mono text-micro text-ink-muted">
        Vitals by Presage · Stored in Tiger Data · Interviewer by Gemini + ElevenLabs
      </footer>

      {stage === 'retry' && <LightOpens />}
      {stage === 'leaving' && <ScreenDims />}
    </div>
  )
}

/** Trying again: warm gold light opening out from the centre until it fills the screen. */
function LightOpens() {
  return (
    <div
      className="animate-iris-open pointer-events-none fixed inset-0 z-50 bg-[radial-gradient(circle_at_50%_45%,rgb(255_243_196)_0%,rgb(212_175_55)_38%,rgb(120_96_28)_100%)]"
      style={{ animationDelay: `${RETRY_HOLD_MS}ms` }}
      aria-hidden="true"
    />
  )
}

/** Leaving: the screen dims to the page colour while the content sinks away. */
function ScreenDims() {
  return <div className="animate-dim pointer-events-none fixed inset-0 z-50 bg-page" aria-hidden="true" />
}

/** After leaving: court is adjourned and the program is finished. */
function CourtAdjourned({ search }: { search: string }) {
  return (
    <div className="bg-monitor grid min-h-dvh animate-fade-in place-items-center p-gutter text-center">
      <div className="flex max-w-lg flex-col items-center gap-5">
        <div className="animate-rise" aria-hidden="true">
          <InterviewerBust size="lg" />
        </div>
        <h1 className="animate-rise text-title font-semibold" style={enter(1)}>
          Court is adjourned.
        </h1>
        <p className="animate-rise text-body text-ink-secondary" style={enter(2)}>
          {INTERVIEWER_NAME} will hear your case again whenever you are ready to take the stand.
        </p>
        <Link to={{ pathname: '/', search }} className="animate-rise" style={enter(3)}>
          <Button variant="secondary">Reopen my case</Button>
        </Link>
      </div>
    </div>
  )
}
