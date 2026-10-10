/**
 * Mock REST API (VITE_MOCK=1). Sessions run in-browser via mockHub; replay and
 * report come from the finished engine, or from the generated fixtures for ids
 * this page load has never seen.
 */
import type { Api } from '../api.ts'
import type { ReplayResponse, Report } from '../types.ts'
import { mockHub } from './hub.ts'
import { buildReplay } from './replay.ts'
import { buildReport } from './report.ts'
import replaySample from './replay_sample.json'
import reportSample from './report_sample.json'

const REPLAY_SAMPLE = replaySample as unknown as ReplayResponse
const REPORT_SAMPLE = reportSample as unknown as Report

const CREATE_DELAY_MS = 250
const REPORT_DELAY_MS = 1_200
const READ_DELAY_MS = 150

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/** structuredClone so callers can't mutate the fixtures. */
const copy = <T>(value: T): T => structuredClone(value)

export const mockApi: Api = {
  async createSession(body) {
    await delay(CREATE_DELAY_MS)
    const sessionId = crypto.randomUUID()
    mockHub.create(sessionId, body)
    return { session_id: sessionId }
  },

  async startSession(sessionId) {
    await delay(READ_DELAY_MS)
    mockHub.start(sessionId)
  },

  async getActiveSession() {
    await delay(READ_DELAY_MS)
    const engine = mockHub.active()
    return engine ? { session_id: engine.sessionId } : null
  },

  async getReplay(sessionId) {
    await delay(READ_DELAY_MS)
    const engine = mockHub.get(sessionId)
    if (engine && engine.phase === 'ended') return buildReplay(engine.getRecording())
    return copy(REPLAY_SAMPLE)
  },

  async getReport(sessionId) {
    const engine = mockHub.get(sessionId)
    if (!engine) {
      await delay(READ_DELAY_MS)
      return copy(REPORT_SAMPLE)
    }
    if (engine.phase !== 'ended') {
      await delay(READ_DELAY_MS)
      return null
    }
    // Simulates Gemini's final_report latency.
    await delay(REPORT_DELAY_MS)
    const recording = engine.getRecording()
    return buildReport(buildReplay(recording), recording.flags)
  },
}
