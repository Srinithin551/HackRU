/**
 * Artificial Presage for testing without a camera: `node sensor.mjs --simulate`.
 *
 * Behaves like the SmartSpectra SDK object the sensor uses (same events, same protobuf metrics buffers,
 * validation codes only on change, pulse every 2-4 s), so everything after Presage runs for real.
 * Like the real one, the sensor starts it when an interview starts, so calibration sees a calm baseline:
 *
 *   0-3 s     no face found
 *   3-9 s     face found, Presage warming up (no pulse yet)
 *   9-50 s    calm: HR ~70, breathing ~14 (calibration happens here)
 *   50 s on   60 s cycles: calm, "hold still" at 12-15 s, a 25 s stress spike from 30 s (HR ~98,
 *             breathing ~21), and in every other cycle the face is lost at 22-26 s
 *
 * Also mixed in: 1 in 8 pulses at a weak 30% confidence (Data drops those), and junk breathing values at
 * 10% confidence (the sensor must ignore them).
 */
import messages from '@smartspectra/node-sdk/messages'

const { Metrics } = messages.presage.smartspectra

const CALM_UNTIL_S = 50
const CYCLE_S = 60
const TICK_MS = 250

// ProcessingStatus / ValidationCode values from @smartspectra/node-sdk
const IDLE = 1
const STARTING = 2
const RUNNING = 3
const OK = 0
const NO_FACE = 1
const MOTION = 12

/** Small seeded PRNG (mulberry32), so a run is reproducible. */
function prng(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Where the scenario is at `t` seconds: what Presage would see. */
export function scenarioAt(t) {
  if (t < 3) return { validation: NO_FACE, stressed: 0 }
  if (t < 9) return { validation: OK, warming: true, stressed: 0 }
  if (t < CALM_UNTIL_S) return { validation: OK, stressed: 0 }
  const cycle = Math.floor((t - CALM_UNTIL_S) / CYCLE_S)
  const o = (t - CALM_UNTIL_S) % CYCLE_S
  if (cycle % 2 === 1 && o >= 22 && o < 26) return { validation: NO_FACE, stressed: 0 }
  const validation = o >= 12 && o < 15 ? MOTION : OK
  // Stress ramps up over 8 s from 30 s, holds, and is gone by 55 s.
  const stressed = o < 30 || o >= 55 ? 0 : Math.min(1, (o - 30) / 8)
  return { validation, stressed }
}

export class SimulatedPresage {
  constructor({ seed = 7 } = {}) {
    this.rand = prng(seed)
    this.listeners = new Map()
    this.timer = null
    this.restart()
  }

  on(event, cb) {
    this.listeners.set(event, cb)
    return this
  }

  _emit(event, ...args) {
    this.listeners.get(event)?.(...args)
  }

  /** Start the scenario over. */
  restart(nowMs = Date.now()) {
    this.t0 = nowMs
    this.validation = null
    this.nextPulseAt = 0
    this.nextBreathAt = 0
    this.nextJunkAt = 0
    this.pulseCount = 0
  }

  start() {
    clearInterval(this.timer)
    this.restart()
    this._emit('processingStatus', STARTING)
    this._emit('processingStatus', RUNNING)
    this.timer = setInterval(() => this.step(Date.now()), TICK_MS)
  }

  /** One tick at wall-clock `nowMs`: emits whatever Presage would have emitted by then. */
  step(nowMs) {
    const t = (nowMs - this.t0) / 1000
    const s = scenarioAt(t)
    const tsUs = nowMs * 1000
    if (s.validation !== this.validation) {
      this.validation = s.validation
      this._emit('validationStatus', s.validation, tsUs, s.validation === OK ? 'Hold still and record' : 'Simulated')
    }
    if (s.validation === NO_FACE || s.warming) return

    const r = this.rand
    const metrics = {}
    if (t >= this.nextPulseAt) {
      this.nextPulseAt = t + 2 + r() * 2
      this.pulseCount += 1
      const calm = 70 + 2 * Math.sin(t / 7) + (r() - 0.5) * 3
      const hr = calm + s.stressed * (28 + (r() - 0.5) * 4)
      const confidence = this.pulseCount % 8 === 0 ? 30 : 45 + r() * 35
      metrics.cardio = { pulseRate: [{ value: hr, confidence, stable: confidence >= 40, timestamp: tsUs }] }
    }
    const breaths = []
    if (t >= this.nextBreathAt) {
      this.nextBreathAt = t + 4 + r() * 2
      const br = 14 + (r() - 0.5) * 1.6 + s.stressed * 7
      const confidence = 50 + r() * 20
      breaths.push({ value: br, confidence, stable: true, timestamp: tsUs })
    }
    if (t >= this.nextJunkAt) {
      this.nextJunkAt = t + 11
      breaths.push({ value: 45, confidence: 10, stable: false, timestamp: tsUs + 1 })
    }
    if (breaths.length) metrics.breathing = { rate: breaths }
    if (metrics.cardio || metrics.breathing) {
      this._emit('metrics', Buffer.from(Metrics.encode(metrics).finish()), tsUs)
    }
  }

  async stopAsync() {
    clearInterval(this.timer)
    this.timer = null
    this._emit('processingStatus', IDLE)
  }

  async destroy() {
    await this.stopAsync()
  }
}
