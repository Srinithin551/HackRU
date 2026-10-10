/**
 * Everything the live screen knows about the interview, driven by the WebSocket.
 *
 * Flow (PLAN.md §2 + brain.md "As built"):
 *  1. Mount: open /ws/{id}, then POST /start (deduped, and idempotent on the backend).
 *  2. `question`: show it, play its audio. When the audio ENDS, send answer_started and
 *     call onAnswerStart (the page opens the mic). The mic never hears the AI's voice.
 *  3. The page calls finishAnswer(transcript) on "I'm done" or silence → answer_done.
 *  4. `filler`: play it, question text unchanged. `state`: drives the status pill.
 *  5. `vitals`: ring buffer of the last 60 readings for the sparklines.
 *  6. `coach_flag`: 4 s toast, also saved for the report chart.
 *  7. `session_ended`: the report is already saved; onEnded navigates to it.
 *
 * The server replays the latest state/question when a socket (re)connects, so a
 * question we already have is ignored instead of replaying its audio mid-answer.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react'
import { startSession } from '../api.ts'
import { TOAST_MS, VITALS_WINDOW } from '../lib/constants.ts'
import { saveFlag } from '../lib/flagStore.ts'
import { playClip } from '../lib/playClip.ts'
import { createSessionSocket, type SessionSocket, type SocketStatus } from '../lib/socket.ts'
import type { CoachFlagEvent, QuestionEvent, ServerEvent, SessionState, VitalsEvent } from '../types.ts'

export interface VitalsSample {
  /** epoch ms */
  t: number
  hr: number | null
  br: number | null
  focus: number | null
  stress: number | null
}

export interface LiveState {
  socket: SocketStatus
  phase: SessionState | null
  question: QuestionEvent | null
  /** Every question asked so far, in order (for the side list). */
  asked: QuestionEvent[]
  /** Interviewer audio is playing. */
  speaking: boolean
  /** answer_started sent, answer_done not yet: the user's turn. */
  answering: boolean
  vitals: VitalsSample[]
  latest: VitalsEvent | null
  /** When the last vitals event arrived (epoch ms), for "sensor connected". */
  lastVitalsAt: number | null
  /** When calibration began (epoch ms), for the warm-up ring. */
  calibrationStartedAt: number | null
  flags: CoachFlagEvent[]
  toast: CoachFlagEvent | null
  ended: boolean
}

const initialState: LiveState = {
  socket: 'connecting',
  phase: null,
  question: null,
  asked: [],
  speaking: false,
  answering: false,
  vitals: [],
  latest: null,
  lastVitalsAt: null,
  calibrationStartedAt: null,
  flags: [],
  toast: null,
  ended: false,
}

type Action =
  | { type: 'socket'; status: SocketStatus }
  | { type: 'event'; event: ServerEvent; at: number }
  | { type: 'speaking'; value: boolean }
  | { type: 'answering'; value: boolean }
  | { type: 'toast-clear'; flag: CoachFlagEvent }

function reducer(state: LiveState, action: Action): LiveState {
  switch (action.type) {
    case 'socket':
      return { ...state, socket: action.status }
    case 'speaking':
      return { ...state, speaking: action.value }
    case 'answering':
      return { ...state, answering: action.value }
    case 'toast-clear':
      return state.toast === action.flag ? { ...state, toast: null } : state
    case 'event':
      return applyEvent(state, action.event, action.at)
  }
}

function applyEvent(state: LiveState, event: ServerEvent, at: number): LiveState {
  switch (event.type) {
    case 'state':
      return {
        ...state,
        phase: event.data.state,
        calibrationStartedAt:
          event.data.state === 'calibrating' && state.calibrationStartedAt === null ? at : state.calibrationStartedAt,
      }
    case 'question': {
      const q = event.data
      return {
        ...state,
        question: q,
        asked: state.asked.some((a) => a.question_id === q.question_id) ? state.asked : [...state.asked, q],
        speaking: true,
        answering: false,
        calibrationStartedAt: q.kind === 'calibration' && state.calibrationStartedAt === null ? at : state.calibrationStartedAt,
      }
    }
    case 'vitals': {
      const v = event.data
      const sample: VitalsSample = {
        t: Date.parse(v.ts) || at,
        hr: v.heart_rate,
        br: v.breathing_rate,
        focus: v.focus,
        stress: v.stress,
      }
      return { ...state, vitals: [...state.vitals, sample].slice(-VITALS_WINDOW), latest: v, lastVitalsAt: at }
    }
    case 'coach_flag':
      return { ...state, flags: [...state.flags, event.data], toast: event.data }
    case 'session_ended':
      return { ...state, ended: true, answering: false, speaking: false }
    case 'filler':
      return state
  }
}

interface Callbacks {
  /** Interviewer finished speaking: open the mic. */
  onAnswerStart?: (question: QuestionEvent) => void
  /** A new question arrived: close/reset the mic from the previous one. */
  onQuestion?: (question: QuestionEvent) => void
  /** session_ended: the report is ready. */
  onEnded?: () => void
}

export function useLiveSession(sessionId: string, callbacks: Callbacks = {}) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const socketRef = useRef<SessionSocket | null>(null)
  const questionIdRef = useRef<string | null>(null)
  const answeredRef = useRef(new Set<string>())
  // Latest callbacks without re-opening the socket when the page re-renders.
  const callbacksRef = useRef(callbacks)
  useEffect(() => {
    callbacksRef.current = callbacks
  })

  useEffect(() => {
    const socket = createSessionSocket(sessionId)
    socketRef.current = socket
    let stopVoice = () => {}
    let stopFiller = () => {}
    const timers = new Set<ReturnType<typeof setTimeout>>()

    const offStatus = socket.onStatus((status) => dispatch({ type: 'socket', status }))
    const off = socket.subscribe((event) => {
      if (event.type === 'question') {
        const q = event.data
        if (q.question_id === questionIdRef.current) return // replayed on reconnect: already handled
        questionIdRef.current = q.question_id
        dispatch({ type: 'event', event, at: Date.now() })
        callbacksRef.current.onQuestion?.(q)
        stopVoice()
        stopFiller()
        stopVoice = playClip(q.audio_url, () => {
          dispatch({ type: 'speaking', value: false })
          if (questionIdRef.current !== q.question_id) return // superseded by a newer question
          socket.send({ type: 'answer_started', data: { question_id: q.question_id } })
          dispatch({ type: 'answering', value: true })
          callbacksRef.current.onAnswerStart?.(q)
        })
        return
      }

      dispatch({ type: 'event', event, at: Date.now() })
      if (event.type === 'filler') {
        stopFiller()
        stopFiller = playClip(event.data.audio_url)
      } else if (event.type === 'coach_flag') {
        saveFlag(sessionId, event.data)
        const flag = event.data
        timers.add(setTimeout(() => dispatch({ type: 'toast-clear', flag }), TOAST_MS))
      } else if (event.type === 'session_ended') {
        callbacksRef.current.onEnded?.()
      }
    })

    startSession(sessionId).catch((err: unknown) => console.error('[live] POST /start failed', err))

    return () => {
      off()
      offStatus()
      socket.close()
      stopVoice()
      stopFiller()
      timers.forEach(clearTimeout)
      socketRef.current = null
      questionIdRef.current = null
    }
  }, [sessionId])

  /** answer_done for the current question (once). */
  const finishAnswer = useCallback((transcript: string) => {
    const questionId = questionIdRef.current
    const socket = socketRef.current
    if (!questionId || !socket || answeredRef.current.has(questionId)) return
    answeredRef.current.add(questionId)
    socket.send({ type: 'answer_done', data: { question_id: questionId, transcript: transcript.trim() } })
    dispatch({ type: 'answering', value: false })
  }, [])

  const endSession = useCallback(() => {
    socketRef.current?.send({ type: 'end_session', data: {} })
  }, [])

  return { ...state, finishAnswer, endSession }
}
