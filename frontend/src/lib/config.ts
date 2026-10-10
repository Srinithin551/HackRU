/** Runtime configuration from Vite env vars (see frontend/.env.example). */

const env = import.meta.env

/** `VITE_MOCK=1` runs the whole app against src/mocks/, with no backend. */
export const IS_MOCK = env.VITE_MOCK === '1' || env.VITE_MOCK === 'true'

/**
 * Backend origin. Empty (the default) means same origin: in dev, Vite proxies
 * /api, /audio and /ws to VITE_PROXY_TARGET (vite.config.ts). That avoids CORS
 * and lets Web Audio analyse the interviewer's voice for the waveform.
 * Set it (e.g. "http://localhost:8000") to call a backend directly.
 */
export const API_BASE = (env.VITE_API_URL ?? '').trim().replace(/\/+$/, '')

const ABSOLUTE_URL = /^[a-z][a-z\d+.-]*:/i

/** Resolves a backend path ("/api/...", "/audio/x.mp3") against API_BASE. Absolute URLs pass through. */
export function apiUrl(path: string): string {
  if (ABSOLUTE_URL.test(path)) return path
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

/**
 * Where to load interviewer audio from. Brain sends absolute URLs built from its
 * PUBLIC_BASE_URL (e.g. "http://localhost:8000/audio/ab12.mp3"). In proxy mode
 * (API_BASE empty) we keep only the path, so the browser fetches it from its own
 * origin through the Vite proxy: same origin, so Web Audio can read it for the
 * waveform. Mock audio ("mock-tts:...") and null pass through unchanged.
 */
export function resolveAudioUrl(url: string | null): string | null {
  if (!url || url.startsWith('mock-tts:')) return url
  if (API_BASE) return apiUrl(url)
  try {
    const parsed = new URL(url, window.location.origin)
    return parsed.pathname.startsWith('/audio/') ? parsed.pathname : url
  } catch {
    return url
  }
}

/** WebSocket URL for a session: ws(s):// derived from API_BASE, or from the page origin. */
export function wsUrl(sessionId: string): string {
  const origin = API_BASE
    ? API_BASE.replace(/^http/i, 'ws')
    : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
  return `${origin}/ws/${encodeURIComponent(sessionId)}`
}
