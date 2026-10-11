/**
 * Live interview screen (/session/:id). One screen, no scrolling on a laptop or projector:
 *
 *   ┌──────────────┬──────────────────────────────┬──────────────┐
 *   │ Interviewer  │                              │ Vitals       │
 *   │ Q 2 of 5     │      YOUR CAMERA (mirrored)  │ HR (big)     │
 *   │ question     │   warm-up / coach toasts     │ stress gauge │
 *   │ [I'm done]   │   live captions of you       │ sparklines   │
 *   │ asked so far │                              │ breath/focus │
 *   └──────────────┴──────────────────────────────┴──────────────┘
 *
 * The protocol lives in useLiveSession (WebSocket), speech in useSpeechToText,
 * the camera in useCamera. On session_ended this page fades to "Analyzing…" and
 * navigates to the report, which is already saved by then.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { CoachingChip, CoachingPopup } from '../components/CoachingPopup.tsx'
import { CalibrationBanner, CoachToast, SensorHint, SimulatedChip, ThinkingDots, Waveform } from '../components/LiveOverlays.tsx'
import { HrSparkline, StressSparkline } from '../components/Sparklines.tsx'
import { StressGauge } from '../components/StressGauge.tsx'
import { AnalyzingScreen, Brand, Button, StatusDot } from '../components/ui.tsx'
import { useCamera, type CameraStatus } from '../hooks/useCamera.ts'
import { useLiveSession, type LiveState } from '../hooks/useLiveSession.ts'
import { useNow } from '../hooks/useNow.ts'
import { useSpeechToText } from '../hooks/useSpeechToText.ts'
import { useTweenedNumber } from '../hooks/useTweenedNumber.ts'
import { INTERVIEWER_NAME, SENSOR_STALE_MS, SENSOR_STATUS_STALE_MS } from '../lib/constants.ts'
import type { QuestionEvent, SensorStatusEvent } from '../types.ts'

export function LivePage() {
  const { id } = useParams()
  if (!id) return <Navigate to="/" replace />
  // key: a different session id remounts everything with fresh state.
  return <LiveSession key={id} sessionId={id} />
}

function LiveSession({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate()
  const { search } = useLocation() // keeps ?mock=auto across pages
  const [leaving, setLeaving] = useState(false)
  const [typed, setTyped] = useState('')
  const { videoRef, status: cameraStatus, enabled: cameraOn, toggle: toggleCamera, retry: retryCamera } = useCamera()
  const now = useNow(500)

  // STT's silence auto-stop needs finishAnswer, which comes from useLiveSession below.
  const finishRef = useRef<(transcript: string) => void>(() => {})
  const stt = useSpeechToText({ onAutoStop: (text) => finishRef.current(text) })

  const live = useLiveSession(sessionId, {
    onQuestion: () => {
      stt.reset()
      setTyped('')
    },
    onAnswerStart: () => {
      if (stt.supported) stt.start()
    },
    onEnded: () => {
      setLeaving(true)
      setTimeout(() => navigate({ pathname: `/session/${sessionId}/report`, search }), 900)
    },
  })
  useEffect(() => {
    finishRef.current = live.finishAnswer
  }, [live.finishAnswer])

  // Typing is the fallback when the browser has no recognizer or the mic is blocked.
  const typedMode = !stt.supported || stt.error !== null
  const onDone = () => {
    const transcript = typedMode ? typed : stt.stop()
    setTyped('')
    live.finishAnswer(transcript)
  }
  const onEnd = () => {
    if (window.confirm("End the interview now? You'll go straight to your report.")) live.endSession()
  }

  const sensorOn = live.lastVitalsAt !== null && now - live.lastVitalsAt < SENSOR_STALE_MS
  // Presage's own status ("Hold still."); dropped if the sensor stops re-sending it.
  const presage = live.sensor && live.sensorAt !== null && now - live.sensorAt < SENSOR_STATUS_STALE_MS ? live.sensor : null
  const calibrating = live.phase === 'calibrating' && !live.ended
  const transcript = typedMode ? typed : stt.text

  return (
    <div className="bg-monitor flex h-dvh flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-hairline px-gutter">
        <Brand />
        <PhasePill live={live} listening={stt.listening} />
        <div className="flex items-center gap-2">
          {live.socket === 'reconnecting' && <span className="text-caption text-rising">Reconnecting…</span>}
          <Button variant="ghost" onClick={toggleCamera} className="hidden sm:inline-flex">
            {cameraOn ? 'Hide camera' : 'Show camera'}
          </Button>
          <Button variant="secondary" onClick={onEnd} disabled={live.ended}>
            End session
          </Button>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 gap-gutter overflow-y-auto p-gutter lg:grid-cols-[minmax(17rem,22rem)_minmax(0,1fr)_minmax(18rem,23rem)] lg:grid-rows-[minmax(0,1fr)] lg:overflow-hidden">
        <InterviewerPanel
          live={live}
          typedMode={typedMode}
          typed={typed}
          onTyped={setTyped}
          onDone={onDone}
          sttHint={stt.supported && !stt.error}
          sttError={stt.error}
        />

        <section
          aria-label="Your camera"
          className="relative order-first min-h-[18rem] overflow-hidden rounded-panel border border-hairline bg-surface-1 lg:order-none"
        >
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className={`size-full -scale-x-100 object-cover transition-opacity duration-500 ${cameraStatus === 'live' ? 'opacity-100' : 'opacity-0'}`}
          />
          {cameraStatus !== 'live' && <CameraPlaceholder status={cameraStatus} onRetry={retryCamera} onEnable={toggleCamera} />}

          {/* Legibility scrims behind the overlays (not a background treatment). */}
          <div className="pointer-events-none absolute inset-x-0 top-0 h-32 bg-gradient-to-b from-page/70 to-transparent" />
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-44 bg-gradient-to-t from-page/80 to-transparent" />

          <div className="absolute inset-x-gutter top-gutter flex flex-wrap items-center justify-between gap-2">
            <span className="rounded-full border border-hairline bg-page/60 px-3 py-1 font-mono text-micro uppercase tracking-[0.08em] text-ink-secondary backdrop-blur">
              Preview only · video isn&apos;t recorded
            </span>
            {presage?.simulated && <SimulatedChip />}
          </div>

          <div className="absolute inset-x-0 top-14 flex flex-col items-center gap-3 px-gutter">
            {calibrating && (
              <CalibrationBanner
                startedAt={live.calibrationStartedAt}
                now={now}
                // Small talk done but the baseline needs a few more seconds: Brain waits quietly.
                quiet={live.question !== null && !live.speaking && !live.answering}
              />
            )}
            {presage && <SensorHint sensor={presage} />}
            {live.toast && <CoachToast key={live.toast.ts + live.toast.message} flag={live.toast} />}
          </div>

          <div className="absolute inset-x-0 bottom-0 p-gutter">
            <div className="mx-auto max-w-3xl rounded-control border border-hairline bg-page/75 px-4 py-3 backdrop-blur-md">
              <div className="flex items-center justify-between gap-3">
                <span className="label-caps">You</span>
                <StatusDot on={stt.listening}>{stt.listening ? 'Mic on' : 'Mic off'}</StatusDot>
              </div>
              <p className={`mt-1 line-clamp-3 min-h-[1.5em] text-lead ${transcript ? 'text-ink' : 'text-ink-muted'}`}>
                {transcript || captionHint(live, typedMode)}
              </p>
            </div>
          </div>
        </section>

        <VitalsPanel live={live} sensorOn={sensorOn} presage={presage} />
      </main>

      {leaving && <AnalyzingScreen />}
      {live.startError && (
        <div className="bg-monitor fixed inset-0 z-50 grid place-items-center p-gutter text-center">
          <div className="panel max-w-md p-panel">
            <p className="text-title font-semibold">This interview can&apos;t start</p>
            <p className="mt-2 text-body text-ink-secondary">{live.startError}</p>
            <Link to={{ pathname: '/', search }} className="mt-5 inline-block">
              <Button>New session</Button>
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}

function captionHint(live: LiveState, typedMode: boolean): string {
  if (live.ended) return 'Interview complete.'
  if (live.speaking) return 'Listen to the question…'
  if (live.answering) return typedMode ? 'Type your answer in the panel on the left.' : 'Listening… start talking whenever you are ready.'
  if (live.phase === 'thinking') return `${INTERVIEWER_NAME} is thinking…`
  return 'Waiting for the interviewer…'
}

/** One-line status in the header. */
function PhasePill({ live, listening }: { live: LiveState; listening: boolean }) {
  let text: string
  if (live.ended) text = 'Interview complete'
  else if (!live.question && live.phase === null) text = 'Connecting…'
  else if (live.speaking) text = `${INTERVIEWER_NAME} is speaking`
  else if (live.answering) text = listening ? 'Your turn · listening' : 'Your turn'
  else if (live.phase === 'thinking') text = `${INTERVIEWER_NAME} is thinking`
  else if (live.phase === 'calibrating') text = 'Warm-up'
  else text = 'Getting ready'
  return (
    <div className="hidden items-center gap-2 rounded-full border border-hairline bg-surface-1 px-3.5 py-1.5 md:flex">
      <span className={`size-2 rounded-full ${live.answering ? 'animate-pulse bg-accent' : 'bg-ink-muted'}`} />
      <span className="text-caption font-medium text-ink-secondary">{text}</span>
    </div>
  )
}

function counterLabel(q: QuestionEvent | null): string {
  if (!q) return 'Starting'
  if (q.kind === 'calibration') return 'Warm-up'
  return `Question ${q.index} of ${q.total}`
}

function InterviewerPanel({
  live,
  typedMode,
  typed,
  onTyped,
  onDone,
  sttHint,
  sttError,
}: {
  live: LiveState & { finishAnswer: (t: string) => void }
  typedMode: boolean
  typed: string
  onTyped: (value: string) => void
  onDone: () => void
  sttHint: boolean
  sttError: string | null
}) {
  const q = live.question
  const earlier = live.asked.filter((a) => a.question_id !== q?.question_id)
  // Coaching pops up while the interviewer is speaking it, then docks under the question for the user's
  // turn. Keyed by question id so a new coaching turn always pops up again.
  const [closedFor, setClosedFor] = useState<string | null>(null)
  const [reopenedFor, setReopenedFor] = useState<string | null>(null)
  const qid = q?.question_id ?? null
  const coachingOpen =
    Boolean(q?.coaching) && ((live.speaking && closedFor !== qid) || (reopenedFor === qid && closedFor !== qid))
  const closeCoaching = useCallback(() => {
    setClosedFor(qid)
    setReopenedFor(null)
  }, [qid])
  const openCoaching = () => {
    setClosedFor(null)
    setReopenedFor(qid)
  }
  return (
    <aside className="panel relative flex min-h-0 flex-col gap-5 p-panel">
      {q?.coaching && coachingOpen && <CoachingPopup text={q.coaching} speaking={live.speaking} onClose={closeCoaching} />}
      {/* Who is talking */}
      <div className="flex items-center gap-3">
        <div className="grid size-11 shrink-0 place-items-center rounded-full bg-accent-subtle text-lead font-semibold text-accent">
          {INTERVIEWER_NAME[0]}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-body font-semibold">{INTERVIEWER_NAME}</p>
          <p className="text-caption text-ink-muted">AI interviewer</p>
        </div>
        {live.phase === 'thinking' && !live.speaking ? <ThinkingDots /> : <Waveform active={live.speaking} />}
      </div>

      {/* When space is tight, "Asked so far" gives up its space first, then this scrolls. The answer
          controls below never move off screen. */}
      <div className="min-h-0 overflow-y-auto border-t border-hairline pt-5">
        <div className="flex items-center justify-between gap-2">
          <span className="label-caps">{counterLabel(q)}</span>
          {q?.kind === 'follow_up' && (
            <span className="rounded-full bg-accent-subtle px-2 py-0.5 font-mono text-micro uppercase text-accent">Follow-up</span>
          )}
        </div>
        {q && q.kind !== 'calibration' && q.total > 0 && (
          <div className="mt-2.5 flex gap-1.5" aria-hidden="true">
            {Array.from({ length: q.total }, (_, i) => (
              <span
                key={i}
                className={`h-1 flex-1 rounded-full ${i + 1 < q.index ? 'bg-ink-secondary' : i + 1 === q.index ? 'bg-accent' : 'bg-surface-3'}`}
              />
            ))}
          </div>
        )}
        <p className="mt-3 text-question font-medium tracking-tight text-balance">
          {q ? q.text : 'Connecting to your interviewer…'}
        </p>
        {/* Docked coaching note (the pop-up shows it in full while it's being spoken) */}
        {q?.coaching && !coachingOpen && <CoachingChip text={q.coaching} onOpen={openCoaching} />}
      </div>

      {/* Answer controls */}
      <div className="flex shrink-0 flex-col gap-2.5">
        {typedMode && (
          <textarea
            value={typed}
            onChange={(e) => onTyped(e.target.value)}
            disabled={!live.answering}
            rows={3}
            placeholder={live.answering ? 'Type your answer…' : 'Wait for the question to finish'}
            className="w-full resize-none rounded-control border border-border-strong bg-surface-2 px-3 py-2 text-body text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-accent disabled:opacity-50"
          />
        )}
        <Button onClick={onDone} disabled={!live.answering} className="w-full py-3 text-lead">
          I'm done ▶
        </Button>
        <p className="text-caption text-ink-muted">
          {sttError ??
            (sttHint
              ? 'Or just stop talking: your answer ends after 3.5 s of silence.'
              : 'Speech-to-text needs Chrome. You can type your answer instead.')}
        </p>
      </div>

      {/* Asked so far */}
      {earlier.length > 0 && (
        <div className="mt-auto min-h-0 shrink-[100] overflow-y-auto border-t border-hairline pt-4">
          <p className="label-caps">Asked so far</p>
          <ol className="mt-2 flex flex-col gap-2">
            {earlier.map((a) => (
              <li key={a.question_id} className="flex gap-2 text-caption text-ink-muted">
                <span className="shrink-0 font-mono text-ink-secondary">{a.kind === 'calibration' ? 'W' : `Q${a.index}`}</span>
                <span className="line-clamp-2">{a.text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </aside>
  )
}

function CameraPlaceholder({ status, onRetry, onEnable }: { status: CameraStatus; onRetry: () => void; onEnable: () => void }) {
  const copy: Record<CameraStatus, string> = {
    starting: 'Starting your camera…',
    live: '',
    denied: 'Camera access is blocked. Allow it from the address bar to see yourself. Your vitals still come from the sensor.',
    unavailable: 'No camera found. Your vitals still come from the sensor.',
    off: 'Camera preview is off.',
  }
  return (
    <div className="absolute inset-0 grid place-items-center p-gutter">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <svg viewBox="0 0 48 48" className="size-12 text-ink-disabled" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <rect x="4" y="12" width="30" height="24" rx="5" />
          <path d="m34 21 10-6v18l-10-6" strokeLinejoin="round" />
        </svg>
        <p className="text-body text-ink-secondary">{copy[status]}</p>
        {(status === 'denied' || status === 'unavailable') && (
          <Button variant="secondary" onClick={onRetry}>
            Try again
          </Button>
        )}
        {status === 'off' && (
          <Button variant="secondary" onClick={onEnable}>
            Show camera
          </Button>
        )}
      </div>
    </div>
  )
}

function sensorLabel(sensorOn: boolean, everHadVitals: boolean, presage: SensorStatusEvent | null): string {
  if (sensorOn) return presage?.simulated ? 'Simulated sensor' : 'Sensor connected'
  if (presage?.state === 'starting' || presage?.state === 'warming_up') return 'Presage starting…'
  if (presage?.state === 'error') return 'Presage error'
  return everHadVitals ? 'No signal' : 'Waiting for sensor'
}

function VitalsPanel({ live, sensorOn, presage }: { live: LiveState; sensorOn: boolean; presage: SensorStatusEvent | null }) {
  const v = live.latest
  const hr = useTweenedNumber(sensorOn ? (v?.heart_rate ?? null) : null)
  const stress = sensorOn ? (v?.stress ?? null) : null
  const beatSeconds = hr && hr > 20 ? 60 / hr : null
  const focus = sensorOn ? (v?.focus ?? null) : null
  const breathing = sensorOn ? (v?.breathing_rate ?? null) : null

  return (
    <aside className="panel flex min-h-0 flex-col gap-4 overflow-y-auto p-panel">
      <div className="flex items-center justify-between">
        <span className="label-caps">Live vitals</span>
        <StatusDot on={sensorOn}>{sensorLabel(sensorOn, live.lastVitalsAt !== null, presage)}</StatusDot>
      </div>

      <div>
        <div className="flex items-center justify-between">
          <span className="label-caps">Heart rate · bpm</span>
          {/* Pulses once per heartbeat: animation length = 60 / bpm seconds. */}
          <span
            className={`size-3 rounded-full ${beatSeconds ? 'animate-beat bg-series-hr' : 'bg-ink-disabled'}`}
            style={beatSeconds ? { animationDuration: `${beatSeconds}s` } : undefined}
            aria-hidden="true"
          />
        </div>
        <p className="mt-1 text-hero font-semibold tracking-tight tabular-nums">{hr === null ? '—' : Math.round(hr)}</p>
        <div className="mt-2 h-14">
          <HrSparkline data={live.vitals} />
        </div>
      </div>

      <div className="border-t border-hairline pt-4">
        <div className="flex items-center justify-between">
          <span className="label-caps">Stress · 0–100</span>
          <span className="font-mono text-micro text-ink-muted">60 s</span>
        </div>
        <div className="mt-2">
          <StressGauge
            score={stress}
            emptyLabel={!sensorOn ? 'No signal' : live.phase === 'calibrating' ? 'Calibrating…' : 'No reading'}
          />
        </div>
        <div className="mt-2 h-12">
          <StressSparkline data={live.vitals} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 border-t border-hairline pt-4">
        <div>
          <span className="label-caps">Breathing</span>
          <p className="mt-1 text-title font-semibold tabular-nums">
            {breathing === null ? '—' : breathing.toFixed(0)}
            <span className="ml-1 text-caption font-normal text-ink-muted">/min</span>
          </p>
        </div>
        <div>
          <span className="label-caps">Focus</span>
          <p className="mt-1 text-title font-semibold tabular-nums">{focus === null ? '—' : `${Math.round(focus * 100)}%`}</p>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-accent-subtle">
            <div
              className="h-full rounded-full bg-series-focus transition-[width] duration-700 ease-out"
              style={{ width: `${focus === null ? 0 : Math.round(focus * 100)}%` }}
            />
          </div>
        </div>
      </div>
    </aside>
  )
}
