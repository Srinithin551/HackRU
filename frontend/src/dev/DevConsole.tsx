/**
 * Dev Console: a temporary, unstyled page for testing the frontend ↔ backend wiring
 * before the real Setup / Live / Report pages exist. It is the browser twin of
 * Brain's scripts/fake_frontend.py.
 *
 * What it does, top to bottom:
 *  1. Shows the resolved config (mock or real, API base, WebSocket URL) and GET /health
 *     (is BRAIN_FAKE on, are Gemini/ElevenLabs keyed, which routers are mounted).
 *  2. Create + start a session: POST /api/sessions, open /ws/{id}, POST /start.
 *  3. Follows the live protocol exactly like the real Live page will:
 *     question → play audio → on audio end send answer_started → you type the answer →
 *     "I'm done" sends answer_done. Fillers are played; vitals and coach flags are shown.
 *  4. On session_ended, fetches /report (polling) and /replay and prints a summary.
 *
 * Speech-to-text isn't wired yet (that's the useSpeechToText hook), so the answer is typed.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import './dev.css'
import { createSession, getHealth, getReplay, startSession, waitForReport } from '../api.ts'
import { API_BASE, IS_MOCK, wsUrl } from '../lib/config.ts'
import { APP_NAME } from '../lib/constants.ts'
import { playClip } from '../lib/playClip.ts'
import { createSessionSocket, type SessionSocket, type SocketStatus } from '../lib/socket.ts'
import {
  INTERVIEW_TYPES,
  type CoachFlagEvent,
  type HealthResponse,
  type InterviewType,
  type QuestionEvent,
  type ReplayResponse,
  type Report,
  type ServerEvent,
  type SessionState,
  type VitalsEvent,
} from '../types.ts'

const MAX_LOG = 60
const stamp = () => new Date().toLocaleTimeString()

/** One-line description of an event for the log (vitals are summarized, not logged). */
function describe(event: ServerEvent): string {
  switch (event.type) {
    case 'state':
      return `state → ${event.data.state}`
    case 'question':
      return `question [${event.data.kind} ${event.data.index}/${event.data.total}] ${event.data.text}`
    case 'filler':
      return `filler ${event.data.audio_url}`
    case 'coach_flag':
      return `coach_flag (${event.data.level}) ${event.data.message}`
    case 'sensor':
      return `sensor ${event.data.state}${event.data.message ? `: ${event.data.message}` : ''}`
    case 'session_ended':
      return 'session_ended'
    case 'vitals':
      return 'vitals'
  }
}

export function DevConsole() {
  // Setup form
  const [userName, setUserName] = useState('Shreyas')
  const [interviewType, setInterviewType] = useState<InterviewType>('demo')
  const [roleTitle, setRoleTitle] = useState('Software Engineer')

  // Backend + session status
  const [health, setHealth] = useState<HealthResponse | string>('checking…')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [socketStatus, setSocketStatus] = useState<SocketStatus | '—'>('—')
  const [state, setState] = useState<SessionState | '—'>('—')
  const [question, setQuestion] = useState<QuestionEvent | null>(null)
  const [answering, setAnswering] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [vitals, setVitals] = useState<VitalsEvent | null>(null)
  const [vitalsCount, setVitalsCount] = useState(0)
  const [flag, setFlag] = useState<CoachFlagEvent | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [replay, setReplay] = useState<ReplayResponse | null>(null)
  const [log, setLog] = useState<string[]>([])

  const socketRef = useRef<SessionSocket | null>(null)
  const stopQuestionAudio = useRef<() => void>(() => {})
  const stopFiller = useRef<() => void>(() => {})
  const lastSensor = useRef('')

  const addLog = useCallback((line: string) => {
    setLog((prev) => [`${stamp()}  ${line}`, ...prev].slice(0, MAX_LOG))
  }, [])

  // 1. Health check on load (and the config it was made with).
  useEffect(() => {
    getHealth().then(setHealth, (err: unknown) => setHealth(`unreachable: ${String(err)}`))
  }, [])

  // 2-4. Everything for one session: socket, /start, live events, report on end.
  useEffect(() => {
    if (!sessionId) return
    const socket = createSessionSocket(sessionId)
    socketRef.current = socket
    const offStatus = socket.onStatus((s) => {
      setSocketStatus(s)
      addLog(`socket ${s}`)
    })

    const off = socket.subscribe((event) => {
      if (event.type === 'vitals') {
        setVitals(event.data)
        setVitalsCount((n) => n + 1)
        return
      }
      if (event.type === 'sensor') {
        // Re-sent every 3 s: log only changes.
        const line = describe(event)
        if (line !== lastSensor.current) addLog(line)
        lastSensor.current = line
        return
      }
      addLog(describe(event))
      switch (event.type) {
        case 'state':
          setState(event.data.state)
          break
        case 'question': {
          const q = event.data
          setQuestion(q)
          setAnswering(false)
          setTranscript('')
          stopQuestionAudio.current()
          // The mic (here: the text box) opens only after the interviewer stops talking,
          // so speech-to-text never transcribes the AI's own voice.
          stopQuestionAudio.current = playClip(q.audio_url, () => {
            socket.send({ type: 'answer_started', data: { question_id: q.question_id } })
            addLog('→ answer_started')
            setAnswering(true)
          })
          break
        }
        case 'filler':
          stopFiller.current()
          stopFiller.current = playClip(event.data.audio_url)
          break
        case 'coach_flag':
          setFlag(event.data)
          setTimeout(() => setFlag((current) => (current === event.data ? null : current)), 4_000)
          break
        case 'session_ended':
          setAnswering(false)
          waitForReport(sessionId).then(setReport, (err: unknown) => addLog(`report failed: ${String(err)}`))
          getReplay(sessionId).then(setReplay, (err: unknown) => addLog(`replay failed: ${String(err)}`))
          break
      }
    })

    startSession(sessionId).then(
      (res) => addLog(`POST /start → ${res.status}`),
      (err: unknown) => addLog(`POST /start failed: ${String(err)}`),
    )

    return () => {
      off()
      offStatus()
      socket.close()
      stopQuestionAudio.current()
      stopFiller.current()
    }
  }, [sessionId, addLog])

  const onCreate = async () => {
    setReport(null)
    setReplay(null)
    setQuestion(null)
    setVitalsCount(0)
    setSocketStatus('connecting') // a new socket always starts here; onStatus reports changes after
    try {
      const res = await createSession({ user_name: userName, interview_type: interviewType, role_title: roleTitle })
      addLog(`POST /api/sessions → ${res.session_id}`)
      setSessionId(res.session_id)
    } catch (err) {
      addLog(`create failed: ${String(err)}`)
    }
  }

  const onDone = () => {
    if (!question || !socketRef.current) return
    socketRef.current.send({ type: 'answer_done', data: { question_id: question.question_id, transcript } })
    addLog(`→ answer_done (${transcript.length} chars)`)
    setAnswering(false)
  }

  const onEnd = () => {
    socketRef.current?.send({ type: 'end_session', data: {} })
    addLog('→ end_session')
  }

  return (
    <main className="dev">
      <h1>{APP_NAME} · Dev Console</h1>
      <p className="muted">
        Temporary page for testing the wiring. Mode: <b>{IS_MOCK ? 'MOCK (no backend)' : 'REAL backend'}</b> · API
        base: <code>{API_BASE || '(same origin → Vite proxy)'}</code> · WebSocket: <code>{wsUrl('SESSION_ID').replace('SESSION_ID', '{session_id}')}</code>
      </p>
      <section>
        <h2>Backend /health</h2>
        <pre>{typeof health === 'string' ? health : JSON.stringify(health)}</pre>
      </section>

      <section>
        <h2>1 · Create session</h2>
        <div className="row">
          <input value={userName} onChange={(e) => setUserName(e.target.value)} placeholder="Name" />
          <select value={interviewType} onChange={(e) => setInterviewType(e.target.value as InterviewType)}>
            {INTERVIEW_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <input value={roleTitle} onChange={(e) => setRoleTitle(e.target.value)} placeholder="Role title" />
          <button onClick={onCreate}>Create + start</button>
          <button onClick={onEnd} disabled={!sessionId}>
            End session
          </button>
        </div>
        <p className="muted">
          session <code>{sessionId ?? '—'}</code> · socket <b>{socketStatus}</b> · state <b>{state}</b>
        </p>
      </section>

      <section>
        <h2>2 · Live</h2>
        <p className="question">
          {question ? (
            <>
              <span className="muted">
                [{question.kind}] Q {question.index} of {question.total}
              </span>{' '}
              {question.text}
            </>
          ) : (
            '—'
          )}
        </p>
        <div className="row">
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder={answering ? 'Type your answer (speech-to-text comes later)…' : 'Waiting for the interviewer to finish…'}
            disabled={!answering}
            rows={3}
          />
          <button onClick={onDone} disabled={!answering}>
            I'm done ▶
          </button>
        </div>
        <p>
          vitals ({vitalsCount}): HR <b>{vitals?.heart_rate ?? '—'}</b> · BR {vitals?.breathing_rate ?? '—'} · stress{' '}
          <b>{vitals?.stress ?? '— (no baseline yet / no face)'}</b>
        </p>
        {flag && <p className={`flag ${flag.level}`}>coach flag ({flag.level}): {flag.message}</p>}
      </section>

      <section>
        <h2>3 · Report + replay</h2>
        {report ? (
          <pre>
            {JSON.stringify(
              {
                composure_score: report.composure_score,
                summary: report.summary,
                hardest_moment: report.hardest_moment,
                generated_by: report.generated_by,
                coach_flags: report.coach_flags?.length,
                top_tips: report.top_tips,
              },
              null,
              2,
            )}
          </pre>
        ) : (
          <p className="muted">Appears after session_ended.</p>
        )}
        {replay && (
          <p>
            replay: {replay.series.length} buckets · {replay.questions.length} questions · baseline HR{' '}
            {replay.session.baseline?.heart_rate ?? '—'}
          </p>
        )}
      </section>

      <section>
        <h2>Event log</h2>
        <pre className="log">{log.join('\n')}</pre>
      </section>
    </main>
  )
}
