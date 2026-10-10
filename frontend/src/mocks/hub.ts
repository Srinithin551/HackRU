/**
 * In-memory registry of mock sessions: the in-browser stand-in for the backend's
 * session table + interview tasks. mockApi creates engines, mockSocket attaches to them.
 */
import type { CreateSessionRequest } from '../types.ts'
import { MockSessionEngine } from './engine.ts'

const engines = new Map<string, MockSessionEngine>()

/** `?mock=auto` makes the engine answer by itself (unattended demos, screenshots). */
export function isAutoAnswer(): boolean {
  try {
    return typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('mock') === 'auto'
  } catch {
    return false
  }
}

export const mockHub = {
  /** A new session (mock POST /api/sessions). Does not start it. */
  create(sessionId: string, body: CreateSessionRequest): MockSessionEngine {
    engines.get(sessionId)?.stop()
    const engine = new MockSessionEngine({
      sessionId,
      userName: body.user_name || 'Guest',
      interviewType: body.interview_type,
      roleTitle: body.role_title,
      autoAnswer: isAutoAnswer(),
    })
    engines.set(sessionId, engine)
    return engine
  },

  get(sessionId: string): MockSessionEngine | undefined {
    return engines.get(sessionId)
  },

  /** Unknown ids (e.g. after a page reload wiped memory) get a fresh demo session. */
  getOrCreate(sessionId: string): MockSessionEngine {
    return (
      engines.get(sessionId) ??
      mockHub.create(sessionId, { user_name: 'Guest', interview_type: 'demo', role_title: 'Software Engineer' })
    )
  },

  /** Mock POST /api/sessions/{id}/start. Idempotent. */
  start(sessionId: string): MockSessionEngine {
    const engine = mockHub.getOrCreate(sessionId)
    engine.start()
    return engine
  },

  /** Most recently created engine that is still running (mock GET /api/sessions/active). */
  active(): MockSessionEngine | null {
    const all = [...engines.values()]
    for (let i = all.length - 1; i >= 0; i--) if (all[i].phase === 'running') return all[i]
    return null
  },
}
