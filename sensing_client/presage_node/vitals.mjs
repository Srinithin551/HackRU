/**
 * Turns Presage SmartSpectra metrics into VitalsReadings (backend/contracts.py), one per second,
 * plus a one-line status for the live page ("Hold still", "No face found", ...).
 *
 * Pure logic (no SDK, no network), so it's unit-tested (test/vitals.test.mjs) and shared by the simulator.
 *
 * Compared with the old log-file client (sensing_client/client.py), every Presage value now arrives with
 * its own confidence and timestamp, so nothing has to be guessed:
 *  - pulse: Presage updates it every few seconds, so the last value is held at its own confidence
 *    for PULSE_HOLD_S instead of decaying ×0.7 per second (doc.md P2);
 *  - breathing: only values at Presage's own "stable" confidence (>= 45) are used (doc.md P1);
 *  - no face: Presage's validation status says so directly.
 */

export const PULSE_HOLD_S = 8         // hold the last pulse this long (Presage updates it every few seconds)
export const SMOOTH_S = 5             // rolling median over values this recent
export const BREATH_MIN_CONFIDENCE = 45   // Presage's "stable" threshold for breathing rate (±1 breath/min)
export const BREATH_HOLD_S = 15       // breathing rate updates slowly; keep the last stable value this long
export const PULSE_MIN_CONFIDENCE = 40    // Presage's "stable" threshold for heart rate (±3 bpm)

/**
 * Coasting window: how long we keep reporting the last pulse after it goes stale.
 *
 * Presage goes quiet for a few seconds fairly often (a turn of the head, a hand near the face, a
 * dark frame). Reporting null the instant PULSE_HOLD_S lapses made the whole chain go void: the
 * backend drops sub-cutoff readings, so the live chart froze, stress went null and the gods lost
 * sight of the mortal mid-answer.
 *
 * Instead, while the face is STILL IN FRAME, we coast: keep emitting the last known pulse with a
 * confidence that decays linearly to zero across this window. A blip stays above the backend's
 * cutoff and the chart keeps moving; a real dropout sinks below it within a few seconds and
 * honestly reads as "no signal". We never coast when the face is gone — that is a real no-reading,
 * and inventing a pulse for an empty chair would be a lie.
 */
export const PULSE_COAST_S = 12

// Presage's ValidationCode values (@smartspectra/node-sdk ValidationCode), with what to tell the user.
export const VALIDATION = {
  0: { name: 'kOk', message: null },
  1: { name: 'kNoFaceFound', message: 'No face found. Sit in front of the camera.' },
  2: { name: 'kMultipleFacesFound', message: 'More than one face in view.' },
  3: { name: 'kFaceNotCentered', message: 'Center your face in the camera.' },
  4: { name: 'kFaceSizeOutOfRange', message: 'Move closer to or farther from the camera.' },
  5: { name: 'kTooDark', message: 'Too dark. Face a light.' },
  6: { name: 'kTooBright', message: 'Too bright. Avoid strong light behind you.' },
  7: { name: 'kChestNotVisible', message: 'Sit back a little so your chest is in view.' },
  10: { name: 'kCameraTuning', message: 'Camera is adjusting…' },
  11: { name: 'kFrameRateTooLow', message: 'Camera frame rate is too low. Add light or close other camera apps.' },
  12: { name: 'kExcessiveMotion', message: 'Hold still.' },
  13: { name: 'kFaceTooClose', message: 'Move back a little.' },
  14: { name: 'kFaceTooFar', message: 'Move closer to the camera.' },
  15: { name: 'kFaceTooHigh', message: 'Move down a little in the frame.' },
  16: { name: 'kFaceTooLow', message: 'Move up a little in the frame.' },
  17: { name: 'kFaceNotForward', message: 'Look straight at the screen.' },
}
export const NO_FACE = 1

/** int64 fields decode as Long objects (protobufjs); plain numbers pass through. */
export function toNumber(v) {
  if (v === null || v === undefined) return 0
  if (typeof v === 'number') return v
  if (typeof v.toNumber === 'function') return v.toNumber()
  return Number(v)
}

function median(values) {
  const s = [...values].sort((a, b) => a - b)
  const mid = s.length >> 1
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2
}

const round = (x, digits) => Math.round(x * 10 ** digits) / 10 ** digits

export class VitalsState {
  constructor() {
    this.active = false      // Presage should be running (an interview is on)
    this.error = null        // what to tell the user when Presage itself failed
    this.reset()
  }

  /** Forget everything measured (Presage stopped or restarted). */
  reset() {
    this.pulse = []    // {at: ms, value: bpm, confidence: 0-1}
    this.breath = []   // same, only stable breathing values
    this.lastPulseTs = 0
    this.lastBreathTs = 0
    this.validation = null   // {code, hint, at}
    this.running = false     // Presage's pipeline reported Running
  }

  /** One decoded Metrics message from the SDK's 'metrics' event. */
  ingestMetrics(metrics, nowMs) {
    const fresh = (list, lastTs) => {
      if (!Array.isArray(list) || list.length === 0) return [[], lastTs]
      const out = []
      let newest = lastTs
      for (const m of list) {
        const ts = toNumber(m.timestamp)
        if (ts && ts <= lastTs) continue   // already seen in an earlier packet
        out.push(m)
        newest = Math.max(newest, ts)
      }
      return [out, newest]
    }
    const [pulses, pTs] = fresh(metrics?.cardio?.pulseRate, this.lastPulseTs)
    this.lastPulseTs = pTs
    for (const p of pulses) {
      if (p.value > 0) this.pulse.push({ at: nowMs, value: p.value, confidence: (p.confidence ?? 0) / 100 })
    }
    const [breaths, bTs] = fresh(metrics?.breathing?.rate, this.lastBreathTs)
    this.lastBreathTs = bTs
    for (const b of breaths) {
      if (b.value > 0 && (b.confidence ?? 0) >= BREATH_MIN_CONFIDENCE) {
        this.breath.push({ at: nowMs, value: b.value, confidence: b.confidence / 100 })
      }
    }
    if (pulses.length || breaths.length) this.error = null
  }

  ingestValidation(code, hint, nowMs) {
    this.validation = { code, hint: hint || null, at: nowMs }
  }

  faceLost() {
    return this.validation?.code === NO_FACE
  }

  _prune(nowMs) {
    const keep = Math.max(PULSE_HOLD_S + PULSE_COAST_S, BREATH_HOLD_S, SMOOTH_S) * 1000
    while (this.pulse.length && nowMs - this.pulse[0].at > keep) this.pulse.shift()
    while (this.breath.length && nowMs - this.breath[0].at > keep) this.breath.shift()
  }

  /** True while we're reporting a stale pulse at reduced confidence (see PULSE_COAST_S). */
  coasting(nowMs) {
    if (this.faceLost() || !this.pulse.length) return false
    const age = (nowMs - this.pulse.at(-1).at) / 1000
    return age > PULSE_HOLD_S && age <= PULSE_HOLD_S + PULSE_COAST_S
  }

  /** The VitalsReading for this second. No face or no recent pulse = all nulls, confidence 0. */
  reading(nowMs, sessionId, wallNowMs = Date.now()) {
    this._prune(nowMs)
    const r = {
      session_id: sessionId ?? null,
      ts: new Date(wallNowMs).toISOString(),
      heart_rate: null,
      breathing_rate: null,
      focus: null,        // Presage doesn't measure focus
      expression: null,   // not requested (faceMetrics would add load for little gain)
      confidence: 0,
    }
    if (this.faceLost()) return r

    const held = this.pulse.filter((p) => nowMs - p.at <= PULSE_HOLD_S * 1000)
    if (held.length === 0) {
      // Pulse has gone stale. Coast on the last value at a decaying confidence rather than
      // reporting nothing, so a two-second blip doesn't blank the chart (see PULSE_COAST_S).
      if (!this.coasting(nowMs)) return r
      const last = this.pulse.at(-1)
      const over = (nowMs - last.at) / 1000 - PULSE_HOLD_S
      const decay = Math.max(0, 1 - over / PULSE_COAST_S)
      r.heart_rate = round(last.value, 1)
      r.confidence = round(Math.min(1, Math.max(0, last.confidence * decay)), 2)
      return r
    }
    const recent = held.filter((p) => nowMs - p.at <= SMOOTH_S * 1000)
    r.heart_rate = round(median((recent.length ? recent : held.slice(-1)).map((p) => p.value)), 1)
    r.confidence = round(Math.min(1, Math.max(0, held.at(-1).confidence)), 2)

    const breath = this.breath.filter((b) => nowMs - b.at <= BREATH_HOLD_S * 1000)
    if (breath.length) {
      const bRecent = breath.filter((b) => nowMs - b.at <= SMOOTH_S * 1000)
      r.breathing_rate = round(median((bRecent.length ? bRecent : breath.slice(-1)).map((b) => b.value)), 1)
    }
    return r
  }

  /**
   * One-line status for the live page: {state, message}.
   *   starting   Presage is starting the camera
   *   warming_up face in view, no pulse yet (Presage needs 10-20 s of a still face)
   *   measuring  pulse coming through at a usable confidence
   *   adjust     framing/lighting/motion problem, or a weak signal
   *   error      Presage itself failed (camera access, API key, network)
   *   idle       no interview, so Presage (and the camera) is off
   */
  status(nowMs) {
    if (this.error) return { state: 'error', message: this.error }
    if (!this.active) return { state: 'idle', message: null }
    if (!this.running) return { state: 'starting', message: 'Starting Presage…' }
    const v = this.validation
    if (v && v.code !== 0) {
      return { state: 'adjust', message: VALIDATION[v.code]?.message ?? v.hint ?? `Presage status ${v.code}` }
    }
    this._prune(nowMs)
    const held = this.pulse.filter((p) => nowMs - p.at <= PULSE_HOLD_S * 1000)
    if (held.length === 0) {
      // Coasting on a stale pulse: say so rather than claiming to be measuring or warming up again.
      if (this.coasting(nowMs)) return { state: 'adjust', message: 'Signal faint. Hold still and face the light.' }
      return { state: 'warming_up', message: 'Reading your pulse… hold still, facing a light.' }
    }
    if (held.at(-1).confidence * 100 < PULSE_MIN_CONFIDENCE) {
      return { state: 'adjust', message: 'Weak signal. Face a light and hold still.' }
    }
    return { state: 'measuring', message: null }
  }
}
