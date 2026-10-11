/**
 * The trial (/session/:id). Two panels, no scrolling on a laptop or projector:
 *
 *   ┌────────────────────┬──────────────────────────────────────┐
 *   │ THE GODS           │   YOUR CAMERA (mirrored)             │
 *   │ the bench          │        ┌───────────────────┐         │
 *   │ who is asking      │        │ interrupt (gold)  │ ◀ here, │
 *   │ the question       │        └───────────────────┘   never │
 *   │ [I'm done]         │   your live captions         in the  │
 *   │ already asked      │                            left panel│
 *   │                    ├──────────────────────────────────────┤
 *   │                    │  heart  │  composure  │  breath      │
 *   └────────────────────┴──────────────────────────────────────┘
 *
 * The protocol lives in useLiveSession (WebSocket), speech in useSpeechToText, the camera in
 * useCamera, and the interrupt rules (reset / never repeat / comply first) in GodInterrupt.
 * On session_ended this page fades out and navigates to the verdict.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { GodInterrupt } from '../components/GodInterrupt.tsx'
import { GodsPanel } from '../components/GodsPanel.tsx'
import { CalibrationBanner, SensorHint, SimulatedChip } from '../components/LiveOverlays.tsx'
import { AnalyzingScreen, Brand, Button, StatusDot } from '../components/ui.tsx'
import { VitalsRow } from '../components/VitalsRow.tsx'
import { useCamera, type CameraStatus } from '../hooks/useCamera.ts'
import { useInterrupt } from '../hooks/useInterrupt.ts'
import { useLiveSession, type LiveState } from '../hooks/useLiveSession.ts'
import { useNow } from '../hooks/useNow.ts'
import { useSpeechToText } from '../hooks/useSpeechToText.ts'
import { godForQuestion, SENSOR_STALE_MS, SENSOR_STATUS_STALE_MS } from '../lib/constants.ts'

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

  // STT's silence auto-stop needs finishAnswer and the interrupt gate, both defined below.
  const finishRef = useRef<(transcript: string) => void>(() => {})
  const interruptPendingRef = useRef(false)
  const stt = useSpeechToText({
    onAutoStop: (text) => {
      // Comply first: a pending command holds the answer back. The transcript is kept, so pressing
      // "I'm done" after acknowledging still submits everything they said.
      if (interruptPendingRef.current) return
      finishRef.current(text)
    },
  })

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

  const god = godForQuestion(live.question?.kind === 'calibration' ? 0 : (live.question?.index ?? 0))
  const { interrupt, pending, acknowledge } = useInterrupt(live.question, live.flags, god.name)
  useEffect(() => {
    interruptPendingRef.current = pending
  }, [pending])

  // Typing is the fallback when the browser has no recognizer or the mic is blocked.
  const typedMode = !stt.supported || stt.error !== null
  const onDone = () => {
    const transcript = typedMode ? typed : stt.stop()
    setTyped('')
    live.finishAnswer(transcript)
  }
  const onEnd = () => {
    if (window.confirm('Leave the palace now? The gods will judge what they have already heard.')) live.endSession()
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
        <PhasePill live={live} listening={stt.listening} godName={god.name} />
        <div className="flex items-center gap-2">
          {live.socket === 'reconnecting' && <span className="text-caption text-rising">Reconnecting…</span>}
          <Button variant="ghost" onClick={toggleCamera} className="hidden sm:inline-flex">
            {cameraOn ? 'Hide camera' : 'Show camera'}
          </Button>
          <Button variant="secondary" onClick={onEnd} disabled={live.ended}>
            Leave the palace
          </Button>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 gap-gutter overflow-y-auto p-gutter lg:grid-cols-[minmax(19rem,25rem)_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)] lg:overflow-hidden">
        <GodsPanel
          question={live.question}
          speaking={live.speaking}
          thinking={live.phase === 'thinking'}
          asked={live.asked}
        >
          {typedMode && (
            <textarea
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              disabled={!live.answering}
              rows={3}
              placeholder={live.answering ? 'Type your answer…' : 'Wait for the god to finish'}
              className="w-full resize-none rounded-control border border-border-strong bg-surface-2 px-3 py-2 text-body text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-accent disabled:opacity-50"
            />
          )}
          <Button onClick={onDone} disabled={!live.answering || pending} className="w-full py-3 text-lead">
            {pending ? 'Obey first…' : "I've answered ▶"}
          </Button>
          <p className={`text-caption ${pending ? 'text-high' : 'text-ink-muted'}`}>
            {pending
              ? "A god has interrupted. Do as they ask, then press “As you command”."
              : (stt.error ??
                (stt.supported
                  ? 'Or simply stop speaking: your answer ends after 3.5 s of silence.'
                  : 'The gods hear you best in Chrome. You can type your answer instead.'))}
          </p>
        </GodsPanel>

        <div className="flex min-h-0 flex-col gap-gutter lg:overflow-hidden">
          <section
            aria-label="Your camera"
            className="relative min-h-[16rem] flex-1 overflow-hidden rounded-panel border border-border-strong bg-surface-2 shadow-[inset_0_0_0_1px_rgb(212_175_55/0.3)]"
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
            <div className="pointer-events-none absolute inset-x-0 top-0 h-32 bg-gradient-to-b from-page/80 to-transparent" />
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-page/85 to-transparent" />

            <div className="absolute left-gutter top-gutter flex max-w-[45%] flex-wrap items-center gap-2">
              <span className="rounded-full border border-hairline bg-surface-1/80 px-3 py-1 font-mono text-micro uppercase tracking-[0.08em] text-ink-secondary backdrop-blur">
                Watched, never recorded
              </span>
              {presage?.simulated && <SimulatedChip />}
            </div>

            {/* The interrupt box: top-right of the camera, over the mortal's own face. */}
            <div className="pointer-events-none absolute right-gutter top-gutter z-20 flex justify-end">
              {interrupt && <GodInterrupt interrupt={interrupt} onAcknowledge={acknowledge} />}
            </div>

            <div className="absolute inset-x-0 top-16 flex flex-col items-center gap-3 px-gutter">
              {calibrating && (
                <CalibrationBanner
                  startedAt={live.calibrationStartedAt}
                  now={now}
                  // Small talk done but the baseline needs a few more seconds: the gods wait quietly.
                  quiet={live.question !== null && !live.speaking && !live.answering}
                />
              )}
              {presage && <SensorHint sensor={presage} />}
            </div>

            <div className="absolute inset-x-0 bottom-0 p-gutter">
              <div className="mx-auto max-w-3xl rounded-control border border-hairline bg-surface-1/85 px-4 py-3 backdrop-blur-md">
                <div className="flex items-center justify-between gap-3">
                  <span className="label-caps">You</span>
                  <StatusDot on={stt.listening}>{stt.listening ? 'Heard' : 'Silent'}</StatusDot>
                </div>
                <p className={`mt-1 line-clamp-3 min-h-[1.5em] text-lead ${transcript ? 'text-ink' : 'text-ink-muted'}`}>
                  {transcript || captionHint(live, typedMode, god.name)}
                </p>
              </div>
            </div>
          </section>

          <VitalsRow live={live} sensorOn={sensorOn} presage={presage} />
        </div>
      </main>

      {leaving && <AnalyzingScreen />}
      {live.startError && (
        <div className="bg-monitor fixed inset-0 z-50 grid place-items-center p-gutter text-center">
          <div className="panel-gold max-w-md p-panel">
            <p className="text-title font-semibold">The trial cannot begin</p>
            <p className="mt-2 text-body text-ink-secondary">{live.startError}</p>
            <Link to={{ pathname: '/', search }} className="mt-5 inline-block">
              <Button>Return to the gates</Button>
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}

function captionHint(live: LiveState, typedMode: boolean, godName: string): string {
  if (live.ended) return 'The trial is over.'
  if (live.speaking) return 'Listen…'
  if (live.answering) return typedMode ? 'Type your answer in the panel on the left.' : 'Speak whenever you are ready.'
  if (live.phase === 'thinking') return `${godName} considers your answer…`
  return 'The gods are taking their seats…'
}

/** One-line status in the header. */
function PhasePill({ live, listening, godName }: { live: LiveState; listening: boolean; godName: string }) {
  let text: string
  if (live.ended) text = 'The trial is over'
  else if (!live.question && live.phase === null) text = 'Approaching the palace…'
  else if (live.speaking) text = `${godName} speaks`
  else if (live.answering) text = listening ? 'Your turn · they are listening' : 'Your turn'
  else if (live.phase === 'thinking') text = `${godName} considers`
  else if (live.phase === 'calibrating') text = 'Taking your measure'
  else text = 'The hall is gathering'
  return (
    <div className="hidden items-center gap-2 rounded-full border border-hairline bg-surface-1 px-3.5 py-1.5 md:flex">
      <span className={`size-2 rounded-full ${live.answering ? 'animate-pulse bg-accent' : 'bg-ink-muted'}`} />
      <span className="text-caption font-medium text-ink-secondary">{text}</span>
    </div>
  )
}

function CameraPlaceholder({ status, onRetry, onEnable }: { status: CameraStatus; onRetry: () => void; onEnable: () => void }) {
  const copy: Record<CameraStatus, string> = {
    starting: 'Opening your eye to the hall…',
    live: '',
    denied: 'Camera access is blocked. Allow it from the address bar to see yourself. The gods still read your vitals.',
    unavailable: 'No camera found. The gods still read your vitals from the sensor.',
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
