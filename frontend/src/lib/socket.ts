/**
 * Live connection to the backend's WebSocket hub (`/ws/{session_id}`).
 *
 * How it works:
 *  - The server pushes JSON messages shaped `{ type, data }` (state, question, filler, vitals,
 *    coach_flag, session_ended). `parseServerEvent` validates each one and drops anything
 *    unknown or malformed, so a backend typo can never crash the page.
 *  - Pages `subscribe()` to events and `send()` client events (answer_started, answer_done,
 *    end_session).
 *  - If the connection drops (venue Wi-Fi, backend restart) it reconnects with exponential
 *    backoff (0.5 s doubling up to 5 s, with jitter). Anything sent meanwhile waits in an
 *    outbox (max 100) and is flushed on reconnect.
 *  - With VITE_MOCK=1, `createSessionSocket` returns the in-browser mock instead; callers
 *    cannot tell the difference because both implement `SessionSocket`.
 */
import { createMockSocket } from '../mocks/mockSocket.ts'
import type { ClientEvent, ServerEvent, ServerEventType } from '../types.ts'
import { IS_MOCK, wsUrl } from './config.ts'

export type SocketStatus = 'connecting' | 'open' | 'reconnecting' | 'closed'

/**
 * One live connection to /ws/{session_id}. The real WebSocket and the mock
 * emitter (VITE_MOCK=1) both implement this, so pages never know which is running.
 */
export interface SessionSocket {
  readonly status: SocketStatus
  /** Every well-formed server event, in arrival order. Returns an unsubscribe function. */
  subscribe(listener: (event: ServerEvent) => void): () => void
  onStatus(listener: (status: SocketStatus) => void): () => void
  /** Queued while disconnected and flushed on (re)connect, so answer_done is never dropped. */
  send(event: ClientEvent): void
  close(): void
}

export function createSessionSocket(sessionId: string): SessionSocket {
  return IS_MOCK ? createMockSocket(sessionId) : createWebSocket(wsUrl(sessionId))
}

const SERVER_EVENT_TYPES: ReadonlySet<string> = new Set<ServerEventType>([
  'state', 'question', 'filler', 'vitals', 'coach_flag', 'sensor', 'session_ended',
])

/** Parses one message. Unknown types and malformed JSON return null (ignored, not fatal). */
export function parseServerEvent(raw: unknown): ServerEvent | null {
  let msg = raw
  if (typeof raw === 'string') {
    try {
      msg = JSON.parse(raw)
    } catch {
      return null
    }
  }
  if (!msg || typeof msg !== 'object') return null
  const { type, data } = msg as { type?: unknown; data?: unknown }
  if (typeof type !== 'string' || !SERVER_EVENT_TYPES.has(type)) return null
  if (!data || typeof data !== 'object') return null
  return { type, data } as ServerEvent
}

const RECONNECT_BASE_MS = 500
const RECONNECT_MAX_MS = 5_000
const MAX_QUEUED = 100

function createWebSocket(url: string): SessionSocket {
  let ws: WebSocket | null = null
  let status: SocketStatus = 'connecting'
  let attempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let closedByUser = false
  const outbox: string[] = []
  const listeners = new Set<(event: ServerEvent) => void>()
  const statusListeners = new Set<(status: SocketStatus) => void>()

  const setStatus = (next: SocketStatus) => {
    if (next === status) return
    status = next
    for (const listener of statusListeners) listener(next)
  }

  const connect = () => {
    const socket = new WebSocket(url)
    ws = socket
    socket.onopen = () => {
      attempts = 0
      setStatus('open')
      while (outbox.length > 0 && socket.readyState === WebSocket.OPEN) socket.send(outbox.shift()!)
    }
    socket.onmessage = (message) => {
      const event = parseServerEvent(message.data)
      if (!event) return
      for (const listener of listeners) {
        try {
          listener(event)
        } catch (err) {
          console.error('[socket] listener failed on', event.type, err)
        }
      }
    }
    // onerror is always followed by onclose, which owns the retry.
    socket.onclose = () => {
      if (ws === socket) ws = null
      if (closedByUser) {
        setStatus('closed')
        return
      }
      setStatus('reconnecting')
      const backoff = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempts++)
      reconnectTimer = setTimeout(connect, backoff * (0.8 + Math.random() * 0.4))
    }
  }

  connect()

  return {
    get status() {
      return status
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    onStatus(listener) {
      statusListeners.add(listener)
      return () => {
        statusListeners.delete(listener)
      }
    },
    send(event) {
      const payload = JSON.stringify(event)
      if (ws?.readyState === WebSocket.OPEN) ws.send(payload)
      else if (outbox.length < MAX_QUEUED) outbox.push(payload)
      else console.warn('[socket] outbox full, dropping', event.type)
    },
    close() {
      closedByUser = true
      clearTimeout(reconnectTimer)
      if (ws) ws.close(1000, 'client closed')
      else setStatus('closed')
    },
  }
}
