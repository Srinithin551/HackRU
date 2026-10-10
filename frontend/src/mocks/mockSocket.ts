/**
 * Mock /ws/{session_id} (VITE_MOCK=1): the same SessionSocket surface as the
 * real WebSocket, wired straight to the in-memory MockSessionEngine.
 */
import type { SessionSocket, SocketStatus } from '../lib/socket.ts'
import type { ClientEvent, ServerEvent } from '../types.ts'
import { mockHub } from './hub.ts'

const CONNECT_DELAY_MS = 120

export function createMockSocket(sessionId: string): SessionSocket {
  const engine = mockHub.getOrCreate(sessionId)
  let status: SocketStatus = 'connecting'
  const listeners = new Set<(event: ServerEvent) => void>()
  const statusListeners = new Set<(status: SocketStatus) => void>()
  // Events that arrive while "connecting" are delivered on open, so a session
  // started before the socket finishes connecting doesn't lose its first question.
  const inbox: ServerEvent[] = engine.phase === 'idle' ? [] : engine.latestEvents()
  const outbox: ClientEvent[] = []

  const setStatus = (next: SocketStatus) => {
    if (next === status) return
    status = next
    for (const listener of [...statusListeners]) listener(next)
  }

  const deliver = (event: ServerEvent) => {
    for (const listener of [...listeners]) {
      try {
        listener(event)
      } catch (err) {
        console.error('[mock socket] listener failed on', event.type, err)
      }
    }
  }

  const detach = engine.subscribe((event) => {
    if (status === 'open') deliver(event)
    else if (status === 'connecting') inbox.push(event)
  })

  const openTimer = setTimeout(() => {
    if (status !== 'connecting') return
    setStatus('open')
    // A listener may close() the socket mid-flush, so re-read the status each pass.
    // (Via a function: TypeScript can't see that setStatus() reassigned it.)
    const isOpen = () => status === 'open'
    while (inbox.length > 0 && isOpen()) deliver(inbox.shift()!)
    while (outbox.length > 0 && isOpen()) engine.receive(outbox.shift()!)
  }, CONNECT_DELAY_MS)

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
      if (status === 'open') engine.receive(event)
      else if (status === 'connecting') outbox.push(event)
    },
    /** Detaches this socket only; the session keeps running, like the real server. */
    close() {
      clearTimeout(openTimer)
      detach()
      inbox.length = 0
      outbox.length = 0
      setStatus('closed')
    },
  }
}
