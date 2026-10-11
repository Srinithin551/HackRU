import type { CoachFlagEvent } from '../types.ts'

/**
 * Coach flags seen live, kept in sessionStorage per session. The report page uses
 * them for the chart's red/amber dots only if neither /report (Brain's coach_flags)
 * nor /replay provides any. Storage can be unavailable (private mode), so every
 * access is guarded and failure just means "no stored flags".
 */
const key = (sessionId: string) => `stresstest:flags:${sessionId}`

/** Forget every stored session's flags. Called when a new interview starts, so nothing from an earlier
 * run lingers in the browser (the launcher empties the database on every run too). */
export function clearAllFlags(): void {
  try {
    for (const k of Object.keys(sessionStorage)) if (k.startsWith('stresstest:flags:')) sessionStorage.removeItem(k)
  } catch {
    // storage unavailable: nothing stored either
  }
}

export function saveFlag(sessionId: string, flag: CoachFlagEvent): void {
  try {
    const flags = loadFlags(sessionId)
    flags.push(flag)
    sessionStorage.setItem(key(sessionId), JSON.stringify(flags))
  } catch {
    // ignore: the report falls back to server-side flags
  }
}

export function loadFlags(sessionId: string): CoachFlagEvent[] {
  try {
    const raw = sessionStorage.getItem(key(sessionId))
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? (parsed as CoachFlagEvent[]) : []
  } catch {
    return []
  }
}
