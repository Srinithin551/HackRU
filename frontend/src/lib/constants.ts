/** App-wide constants that aren't design tokens. */

/**
 * Shown on the interviewer card. No contract field carries it; swap this when Brain
 * picks the ElevenLabs voice (brain.md: "Pick one calm, professional voice").
 */
export const INTERVIEWER_NAME = 'Morgan'

/** Live sparkline length: the last 60 vitals readings (~60 s at 1 Hz). */
export const VITALS_WINDOW = 60

/** Brain's CALIBRATION_MIN_S: calibration lasts at least this long (drives the warm-up ring). */
export const CALIBRATION_MS = 35_000

/** Coach flag toasts stay up this long (frontend.md: "Toast for 4 seconds"). */
export const TOAST_MS = 4_000

/** No vitals for this long = sensor disconnected / no face. */
export const SENSOR_STALE_MS = 3_000

/** The Presage sensor re-sends its status every 3 s; older than this = the sensor isn't running. */
export const SENSOR_STATUS_STALE_MS = 10_000

/** Speech-to-text auto-stop (frontend.md): 3.5 s of silence after at least 3 s of speech. */
export const SILENCE_MS = 3_500
export const MIN_SPEECH_MS = 3_000
