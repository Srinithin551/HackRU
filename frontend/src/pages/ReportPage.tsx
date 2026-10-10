/**
 * Report + replay (/session/:id/report). Reached automatically when the interview ends.
 *
 * Data sources, joined by question id:
 *  - GET /report (Brain/Gemini): composure score, summary, hardest moment, per-question
 *    feedback, top tips, coach_flags. Polled until ready (404 = still generating).
 *  - GET /replay (Data/Tiger Data): 5 s vitals series, question windows, per-question stats.
 *  - Coach flags seen live (sessionStorage), only if neither endpoint has them.
 *
 * Layout (one screen on a laptop/projector, scrolls on small screens):
 *   left:  composure score · where you felt it most · top tips
 *   right: timeline (HR panel over stress panel, shared time axis, question bands, flag dots)
 *          question breakdown (weakest highlighted) │ selected question detail
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useParams } from 'react-router'
import { getReplay, waitForReport } from '../api.ts'
import { Timeline, type FlagView, type QuestionView, type ReportView } from '../components/Timeline.tsx'
import { AnalyzingScreen, Brand, Button, StressBadge, StressIcon } from '../components/ui.tsx'
import { loadFlags } from '../lib/flagStore.ts'
import { color, stressColor, stressLevel } from '../styles/tokens.ts'
import type { CoachFlagEvent, ReplayResponse, Report } from '../types.ts'

export function ReportPage() {
  const { id } = useParams()
  if (!id) return <Navigate to="/" replace />
  return <SessionReport key={id} sessionId={id} />
}

const num = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)
const time = (iso: string | null | undefined): number | null => {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isNaN(t) ? null : t
}

/** Joins report + replay + flags into what the screen draws. */
function buildView(report: Report, replay: ReplayResponse, storedFlags: CoachFlagEvent[]): ReportView {
  const points = replay.series
    .map((p) => ({ t: time(p.t), hr: p.hr, stress: p.stress }))
    .filter((p): p is { t: number; hr: number | null; stress: number | null } => p.t !== null)
  const start = time(replay.session.started_at) ?? points[0]?.t ?? 0
  const lastPoint = points.length ? points[points.length - 1].t + 5_000 : start
  const end = Math.max(lastPoint, time(replay.session.ended_at) ?? lastPoint)

  const feedback = new Map(report.per_question.map((f) => [f.question_id, f]))
  const questions: QuestionView[] = [...replay.questions]
    .sort((a, b) => a.idx - b.idx)
    .map((q) => ({
      id: q.id,
      kind: q.kind,
      // Data's sequential idx (counts follow-ups) labels the bands Q1, Q2... (brain.md).
      label: q.kind === 'calibration' ? 'Warm-up' : `Q${q.idx}`,
      text: q.text,
      transcript: q.transcript,
      start: time(q.answer_started_at),
      end: time(q.answered_at),
      stats: q.stats,
      feedback: feedback.get(q.id) ?? null,
      peak: q.stats?.stress_peak ?? null,
    }))

  // "Where you were weaker": the answered question with the highest stress peak.
  const weakest = questions
    .filter((q) => q.kind !== 'calibration' && num(q.peak))
    .reduce<QuestionView | null>((best, q) => (best === null || (q.peak ?? 0) > (best.peak ?? 0) ? q : best), null)

  const nearestStress = (t: number) =>
    points.reduce<{ d: number; s: number | null }>(
      (best, p) => (Math.abs(p.t - t) < best.d && num(p.stress) ? { d: Math.abs(p.t - t), s: p.stress } : best),
      { d: Infinity, s: null },
    ).s
  const rawFlags: { ts: string; level: 'rising' | 'high'; stress?: number }[] =
    report.coach_flags ?? replay.coach_flags ?? storedFlags
  const flags: FlagView[] = rawFlags
    .map((f) => {
      const t = time(f.ts)
      return t === null ? null : { t, level: f.level, y: num(f.stress) ? f.stress : nearestStress(t) }
    })
    .filter((f): f is FlagView => f !== null)

  return { points, start, end, baselineHr: replay.session.baseline?.heart_rate ?? null, questions, weakestId: weakest?.id ?? null, flags }
}

function SessionReport({ sessionId }: { sessionId: string }) {
  const { search } = useLocation()
  const [data, setData] = useState<{ report: Report; replay: ReplayResponse } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    Promise.all([waitForReport(sessionId, { signal: ctrl.signal }), getReplay(sessionId)]).then(
      ([report, replay]) => setData({ report, replay }),
      (err: unknown) => {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err))
      },
    )
    return () => ctrl.abort()
  }, [sessionId, attempt])

  const view = useMemo(() => (data ? buildView(data.report, data.replay, loadFlags(sessionId)) : null), [data, sessionId])

  if (error) {
    return (
      <div className="bg-monitor grid min-h-dvh place-items-center p-gutter text-center">
        <div className="panel max-w-md p-panel">
          <p className="text-title font-semibold">We couldn't load this report</p>
          <p className="mt-2 text-body text-ink-secondary">{error}</p>
          <div className="mt-5 flex justify-center gap-3">
            <Button
              onClick={() => {
                setError(null)
                setAttempt((n) => n + 1)
              }}
            >
              Try again
            </Button>
            <Link to={{ pathname: '/', search }}>
              <Button variant="secondary">New session</Button>
            </Link>
          </div>
        </div>
      </div>
    )
  }
  if (!data || !view) return <AnalyzingScreen />

  const { report, replay } = data
  const selected = view.questions.find((q) => q.id === (selectedId ?? view.weakestId)) ?? view.questions.find((q) => q.kind !== 'calibration') ?? null

  return (
    <div className="bg-monitor flex min-h-dvh animate-fade-in flex-col lg:h-dvh lg:overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-hairline px-gutter">
        <Brand />
        <p className="hidden text-caption text-ink-secondary md:block">
          Session report{replay.session.user_name ? ` · ${replay.session.user_name}` : ''}
          {report.generated_by === 'fallback' && ' · numbers-only report (Gemini unavailable)'}
        </p>
        <Link to={{ pathname: '/', search }}>
          <Button variant="secondary">New session</Button>
        </Link>
      </header>

      {/* grid-rows-[minmax(0,1fr)] pins the row to the screen height so columns scroll inside instead of overflowing. */}
      <main className="grid min-h-0 flex-1 gap-gutter p-gutter lg:grid-cols-[minmax(18rem,25rem)_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]">
        {/* Left: the verdict (scrolls on its own if a short screen can't fit it all) */}
        <div className="flex min-h-0 flex-col gap-gutter lg:overflow-y-auto">
          <section className="panel shrink-0 p-panel">
            <div className="flex items-center gap-4">
              <ScoreRing score={report.composure_score} />
              <div>
                <p className="label-caps">Composure</p>
                <p className="mt-1 text-title font-semibold">{verdict(report.composure_score)}</p>
                <p className="text-caption text-ink-muted">0–100 · higher is calmer</p>
              </div>
            </div>
            <p className="mt-3 text-body text-ink-secondary">{report.summary}</p>
          </section>

          <section className="panel shrink-0 border-l-4 p-panel" style={{ borderLeftColor: color.high }}>
            <p className="label-caps">Where you felt it most</p>
            <p className="mt-2 text-body font-medium xl:text-lead">{report.hardest_moment}</p>
          </section>

          <section className="panel shrink-0 p-panel">
            <p className="label-caps">Top tips</p>
            <ol className="mt-3 flex flex-col gap-3">
              {report.top_tips.map((tip, i) => (
                <li key={i} className="flex gap-3 text-body">
                  <span className="grid size-6 shrink-0 place-items-center rounded-full bg-accent-subtle font-mono text-caption text-accent">
                    {i + 1}
                  </span>
                  <span>{tip}</span>
                </li>
              ))}
            </ol>
          </section>
        </div>

        {/* Right: the replay */}
        <div className="flex min-h-0 flex-col gap-gutter">
          <section className="panel flex min-h-[20rem] flex-[5] flex-col p-panel">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="label-caps">Replay · click a question band</p>
              <Legend />
            </div>
            <Timeline view={view} selectedId={selected?.id ?? null} onSelect={setSelectedId} />
          </section>

          <div className="grid min-h-0 flex-[4] gap-gutter md:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]">
            <section className="panel flex min-h-0 flex-col p-panel">
              <p className="label-caps">Question breakdown · stress peak</p>
              <ul className="mt-3 flex min-h-0 flex-col gap-1.5 overflow-y-auto pr-1">
                {view.questions.map((q) => (
                  <li key={q.id}>
                    <QuestionRow q={q} active={q.id === selected?.id} weakest={q.id === view.weakestId} onClick={() => setSelectedId(q.id)} />
                  </li>
                ))}
              </ul>
            </section>
            {selected && <QuestionDetail q={selected} />}
          </div>
        </div>
      </main>

      <footer className="shrink-0 px-gutter pb-3 text-center font-mono text-micro text-ink-muted">
        Vitals by Presage · Stored in Tiger Data · Interviewer by Gemini + ElevenLabs
      </footer>
    </div>
  )
}

/** One-line read of the composure score, so the number means something at a glance. */
function verdict(score: number): string {
  if (score >= 80) return 'Steady under pressure'
  if (score >= 60) return 'Mostly composed'
  if (score >= 40) return 'Nerves showed'
  return 'A tough session'
}

function ScoreRing({ score }: { score: number }) {
  const r = 44
  const c = 2 * Math.PI * r
  const clamped = Math.max(0, Math.min(100, score))
  return (
    <div className="relative grid size-24 shrink-0 place-items-center">
      <svg viewBox="0 0 100 100" className="absolute inset-0 size-full -rotate-90" aria-hidden="true">
        <circle cx="50" cy="50" r={r} fill="none" strokeWidth="7" className="stroke-surface-3" />
        <circle
          cx="50"
          cy="50"
          r={r}
          fill="none"
          strokeWidth="7"
          strokeLinecap="round"
          className="stroke-accent"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - clamped / 100)}
        />
      </svg>
      <div className="text-center">
        <span className="text-metric font-semibold tracking-tight">{Math.round(clamped)}</span>
        <span className="block font-mono text-micro text-ink-muted">/ 100</span>
      </div>
    </div>
  )
}

function Legend() {
  const item = (swatch: ReactNode, label: string) => (
    <span className="inline-flex items-center gap-1.5 text-caption text-ink-secondary">
      {swatch}
      {label}
    </span>
  )
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {item(<span className="h-0.5 w-4 rounded-full bg-series-hr" />, 'Heart rate')}
      {item(<span className="h-0 w-4 border-t-2 border-dashed border-ink-muted" />, 'Your baseline')}
      {item(
        <span className="flex gap-0.5">
          <StressIcon level="calm" />
          <StressIcon level="rising" />
          <StressIcon level="high" />
        </span>,
        'Stress zones',
      )}
      {item(<span className="size-2.5 rounded-full border-2 border-high" />, 'Coach flag')}
    </div>
  )
}

function QuestionRow({ q, active, weakest, onClick }: { q: QuestionView; active: boolean; weakest: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-3 rounded-control border px-3 py-2.5 text-left transition-colors ${
        active ? 'border-accent bg-surface-2' : 'border-transparent hover:bg-surface-2'
      }`}
    >
      <span className="w-16 shrink-0 font-mono text-caption whitespace-nowrap text-ink-secondary">{q.label}</span>
      <span className="min-w-0 flex-1">
        <span className="line-clamp-1 text-caption text-ink">{q.text}</span>
        <span className="mt-1.5 block h-1 overflow-hidden rounded-full bg-surface-3">
          <span className="block h-full rounded-full" style={{ width: `${Math.min(100, q.peak ?? 0)}%`, backgroundColor: stressColor(q.peak) }} />
        </span>
      </span>
      <span className="flex w-16 shrink-0 flex-col items-end gap-0.5">
        <span className="font-mono text-caption tabular-nums text-ink">{num(q.peak) ? Math.round(q.peak) : '—'}</span>
        {weakest && <span className="font-mono text-[0.625rem] uppercase tracking-wider text-high">Toughest</span>}
      </span>
    </button>
  )
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-control bg-surface-2 px-3 py-2.5">
      <p className="label-caps">{label}</p>
      <p className="mt-1 text-title font-semibold tabular-nums">{value}</p>
      {sub && <p className="text-caption text-ink-muted">{sub}</p>}
    </div>
  )
}

function QuestionDetail({ q }: { q: QuestionView }) {
  const s = q.stats
  const fmt = (v: number | null | undefined, unit = '') => (num(v) ? `${Math.round(v)}${unit}` : '—')
  const delta = s?.hr_delta_pct
  return (
    <section className="panel flex min-h-0 flex-col p-panel">
      <div className="flex items-center justify-between gap-3">
        <p className="label-caps">
          {q.label}
          {q.kind === 'follow_up' ? ' · follow-up' : ''}
        </p>
        <StressBadge score={q.peak} empty="No stress data" />
      </div>
      <div className="mt-2 min-h-0 flex-1 overflow-y-auto pr-1">
        <p className="text-lead font-medium">{q.text}</p>

        <div className="mt-3 grid grid-cols-2 gap-2 xl:grid-cols-4">
          <Stat label="Avg HR" value={fmt(s?.hr_mean)} sub="bpm" />
          <Stat label="Peak HR" value={fmt(s?.hr_peak)} sub="bpm" />
          <Stat label="Vs base" value={num(delta) ? `${delta > 0 ? '+' : ''}${Math.round(delta)}%` : '—'} sub="heart rate" />
          <Stat
            label="Stress peak"
            value={fmt(s?.stress_peak)}
            sub={num(s?.peak_offset_s) ? `${Math.round(s.peak_offset_s)} s into answer` : stressLevel(s?.stress_peak) ?? undefined}
          />
        </div>

        {q.feedback ? (
          <dl className="mt-4 flex flex-col gap-3">
            <div>
              <dt className="label-caps">What happened</dt>
              <dd className="mt-1 text-body text-ink-secondary">{q.feedback.what_happened}</dd>
            </div>
            <div>
              <dt className="label-caps">Body signal</dt>
              <dd className="mt-1 text-body text-ink-secondary">{q.feedback.body_signal}</dd>
            </div>
            <div className="rounded-control border border-accent/40 bg-accent-subtle px-3 py-2.5">
              <dt className="label-caps">Next time</dt>
              <dd className="mt-1 text-body text-ink">{q.feedback.advice}</dd>
            </div>
          </dl>
        ) : (
          <p className="mt-4 text-caption text-ink-muted">No AI feedback for this one{q.kind === 'calibration' ? ' (warm-up).' : '.'}</p>
        )}

        {q.transcript && (
          <div className="mt-4">
            <p className="label-caps">What you said</p>
            <p className="mt-1 text-body italic text-ink-muted">“{q.transcript}”</p>
          </div>
        )}
      </div>
    </section>
  )
}
