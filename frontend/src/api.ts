/**
 * REST client for the FastAPI backend (endpoints in docs/PLAN.md §3).
 *
 * How it works:
 *  - `request()` wraps fetch: JSON in/out, a 10 s timeout (20 s for replay), and a typed
 *    `ApiError` carrying the HTTP status for non-2xx responses.
 *  - `Api` is an interface with two implementations: `httpApi` (real) and `mockApi`
 *    (src/mocks, used when VITE_MOCK=1). The exported functions below delegate to
 *    whichever is active, so pages just call `createSession(...)`, `getReplay(...)`, etc.
 *  - `startSession` is deduplicated per session id (React StrictMode double-runs effects
 *    in dev, which would otherwise start two interview loops on the backend).
 *  - `waitForReport` polls `/report` until Gemini has produced it. A 404 means "not ready
 *    yet", not "error", and transient failures keep retrying until the deadline.
 */
import { apiUrl, IS_MOCK } from './lib/config.ts'
import { mockApi } from './mocks/mockApi.ts'
import type {
  ActiveSessionResponse,
  CreateSessionRequest,
  CreateSessionResponse,
  MaterialUploadResponse,
  HealthResponse,
  InsightsResponse,
  ReplayResponse,
  Report,
  StartSessionResponse,
} from './types.ts'

/** The REST surface the frontend uses (PLAN.md §3). Implemented over HTTP and by src/mocks/mockApi.ts. */
export interface Api {
  createSession(body: CreateSessionRequest): Promise<CreateSessionResponse>
  /** Attach an imported file to a session before it starts. Rejects with the backend's reason. */
  uploadMaterial(sessionId: string, file: File): Promise<MaterialUploadResponse>
  startSession(sessionId: string): Promise<StartSessionResponse>
  getActiveSession(): Promise<ActiveSessionResponse>
  getReplay(sessionId: string): Promise<ReplayResponse>
  /** Resolves to null while the report is still being generated. */
  getReport(sessionId: string): Promise<Report | null>
  /** Chart notes for the replay tooltips. Empty when they can't be produced. */
  getInsights(sessionId: string): Promise<InsightsResponse>
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const DEFAULT_TIMEOUT_MS = 10_000

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const timeout = AbortSignal.timeout(timeoutMs)
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
    signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout,
  })
  if (!res.ok) throw new ApiError(res.status, `${init.method ?? 'GET'} ${path} failed with ${res.status}`)
  const text = await res.text()
  return (text ? JSON.parse(text) : null) as T
}

const sessionPath = (id: string) => `/api/sessions/${encodeURIComponent(id)}`

/** A file as base64, without the data-URL prefix (the backend takes JSON, so no multipart needed). */
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const url = String(reader.result)
      resolve(url.slice(url.indexOf(',') + 1))
    }
    reader.onerror = () => reject(reader.error ?? new Error('could not read the file'))
    reader.readAsDataURL(file)
  })
}

const httpApi: Api = {
  createSession: (body) => request('/api/sessions', { method: 'POST', body: JSON.stringify(body) }),
  uploadMaterial: async (id, file) => {
    const res = await fetch(apiUrl(`${sessionPath(id)}/material`), {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, data: await fileToBase64(file) }),
      signal: AbortSignal.timeout(60_000),
    })
    const body: unknown = await res.json().catch(() => null)
    if (!res.ok) {
      // The backend says why ("that file is over 10 MB", ".key files aren't supported…").
      const detail = (body as { detail?: unknown } | null)?.detail
      throw new ApiError(res.status, typeof detail === 'string' ? detail : `upload failed with ${res.status}`)
    }
    return body as MaterialUploadResponse
  },
  startSession: (id) => request(`${sessionPath(id)}/start`, { method: 'POST' }),
  getActiveSession: () => request('/api/sessions/active'),
  getReplay: (id) => request(`${sessionPath(id)}/replay`, {}, 20_000),
  getReport: async (id) => {
    try {
      return await request<Report | null>(`${sessionPath(id)}/report`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null
      throw err
    }
  },
  // Gemini writes these on first request, so allow for a slow first call.
  getInsights: (id) => request(`${sessionPath(id)}/insights`, {}, 40_000),
}

const impl: Api = IS_MOCK ? mockApi : httpApi

export const createSession = (body: CreateSessionRequest) => impl.createSession(body)
export const uploadMaterial = (sessionId: string, file: File) => impl.uploadMaterial(sessionId, file)
export const getActiveSession = () => impl.getActiveSession()
export const getReplay = (sessionId: string) => impl.getReplay(sessionId)
export const getReport = (sessionId: string) => impl.getReport(sessionId)

/**
 * Chart notes for the replay tooltips. Decorative: a failure leaves the tooltips showing plain
 * numbers, so this resolves to an empty list instead of rejecting.
 */
export const getInsights = async (sessionId: string): Promise<InsightsResponse> => {
  try {
    return await impl.getInsights(sessionId)
  } catch (err) {
    console.warn('[api] insights unavailable', err)
    return { notes: [] }
  }
}

/**
 * GET /health: which backend pieces are live (fake mode, Gemini, ElevenLabs, mounted routers).
 * In mock mode there is no backend, so it reports a fake one.
 */
export async function getHealth(): Promise<HealthResponse> {
  if (IS_MOCK) return { ok: true, fake: true, gemini: false, elevenlabs: false, routers: ['mock'] }
  return request('/health', {}, 3_000)
}

const startRequests = new Map<string, Promise<StartSessionResponse>>()

/**
 * POST /api/sessions/{id}/start, at most once per session per page load. Brain's
 * endpoint is idempotent too (returns already_running), but deduping here avoids
 * a pointless request when React StrictMode mounts effects twice in dev.
 */
export function startSession(sessionId: string): Promise<StartSessionResponse> {
  let pending = startRequests.get(sessionId)
  if (!pending) {
    pending = impl.startSession(sessionId).catch((err: unknown) => {
      startRequests.delete(sessionId)
      throw err
    })
    startRequests.set(sessionId, pending)
  }
  return pending
}

/** Polls /report until Gemini's report exists. Transient errors are retried until the deadline. */
export async function waitForReport(
  sessionId: string,
  { signal, intervalMs = 1_500, timeoutMs = 120_000 }: { signal?: AbortSignal; intervalMs?: number; timeoutMs?: number } = {},
): Promise<Report> {
  const deadline = Date.now() + timeoutMs
  let lastError: unknown = null
  for (;;) {
    signal?.throwIfAborted()
    try {
      const report = await getReport(sessionId)
      if (report) return report
    } catch (err) {
      if (signal?.aborted) throw err
      lastError = err
    }
    if (Date.now() > deadline) throw lastError ?? new ApiError(408, 'Timed out waiting for the report')
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(resolve, intervalMs)
      signal?.addEventListener('abort', () => {
        clearTimeout(timer)
        reject(signal.reason)
      }, { once: true })
    })
  }
}
