/**
 * Pure, seeded simulation pieces for the mock backend: PRNG + ids, Data's stress
 * score and baseline (PLAN.md §3, data.md), a 1 Hz vitals model mirroring
 * scripts/mock_vitals.py, and Brain's watcher rule (brain.md).
 *
 * DOM-free.
 */
import type { Baseline, CoachLevel, Expression, WindowStats } from '../types.ts'

// ---------------------------------------------------------------------------
// PRNG + ids
// ---------------------------------------------------------------------------

/** Uniform [0, 1) generator. */
export type Rng = () => number

/** mulberry32: tiny, fast, good enough for fake physiology. */
export function mulberry32(seed: number): Rng {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 32-bit FNV-1a hash of a string, for deriving seeds. */
export function hashSeed(text: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/** Seeded generator for a named stream, so streams don't disturb each other. */
export function seededRng(seed: number | string, stream: string): Rng {
  return mulberry32(hashSeed(`${seed}:${stream}`))
}

/** Deterministic uuid-v4-shaped ids drawn from `rng`. */
export function createIdGenerator(rng: Rng): () => string {
  return () => {
    const bytes: number[] = []
    for (let i = 0; i < 16; i++) bytes.push(Math.floor(rng() * 256))
    bytes[6] = (bytes[6] & 0x0f) | 0x40
    bytes[8] = (bytes[8] & 0x3f) | 0x80
    const hex = bytes.map((b) => b.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }
}

/** Standard normal sample (Box-Muller). */
export function gaussian(rng: Rng): number {
  const u1 = rng()
  const u2 = rng()
  return Math.sqrt(-2 * Math.log(1 - u1)) * Math.cos(2 * Math.PI * u2)
}

// ---------------------------------------------------------------------------
// Math helpers
// ---------------------------------------------------------------------------

export const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x))

/** Rounds to `dp` decimals (never returns -0). */
export function round(x: number, dp = 1): number {
  const f = 10 ** dp
  const r = Math.round(x * f) / f
  return r === 0 ? 0 : r
}

/** Rounds, passing null through. */
export const roundOrNull = (x: number | null, dp = 1) => (x === null ? null : round(x, dp))

/** Mean of the non-null values (SQL avg semantics: null when there are none). */
export function mean(values: readonly (number | null | undefined)[]): number | null {
  let sum = 0
  let n = 0
  for (const v of values) {
    if (v === null || v === undefined) continue
    sum += v
    n++
  }
  return n === 0 ? null : sum / n
}

/** Max of the non-null values, or null. */
export function maxOf(values: readonly (number | null | undefined)[]): number | null {
  let best: number | null = null
  for (const v of values) {
    if (v === null || v === undefined) continue
    if (best === null || v > best) best = v
  }
  return best
}

/** Epoch ms → ISO-8601 UTC with 'Z', dropping ".000" on whole seconds ("2026-10-10T18:00:05Z"). */
export function isoUtc(ms: number): string {
  return new Date(ms).toISOString().replace('.000Z', 'Z')
}

const smoothstep = (x: number) => {
  const t = clamp(x, 0, 1)
  return t * t * (3 - 2 * t)
}

// ---------------------------------------------------------------------------
// Stress + baseline (Data)
// ---------------------------------------------------------------------------

/** Readings under this confidence are ignored by every calculation. */
export const MIN_CONFIDENCE = 0.5

export interface BaselineValues {
  heart_rate: number
  breathing_rate: number
}

export interface StressInput {
  heart_rate?: number | null
  breathing_rate?: number | null
  confidence: number
}

/**
 * PLAN.md §3 stress score, 0 to 100. Missing inputs drop out and the remaining
 * weights renormalize. Null without a baseline, under 0.5 confidence, or with no inputs.
 */
export function stressScore(reading: StressInput, baseline: BaselineValues | null): number | null {
  if (!baseline || reading.confidence < MIN_CONFIDENCE) return null
  const parts: { weight: number; value: number }[] = []
  const { heart_rate: hr, breathing_rate: br } = reading
  if (hr !== null && hr !== undefined && baseline.heart_rate > 0) {
    const deltaPct = ((hr - baseline.heart_rate) / baseline.heart_rate) * 100
    parts.push({ weight: 0.5, value: clamp(deltaPct / 25, 0, 1) })
  }
  if (br !== null && br !== undefined && baseline.breathing_rate > 0) {
    const deltaPct = ((br - baseline.breathing_rate) / baseline.breathing_rate) * 100
    parts.push({ weight: 0.3, value: clamp(deltaPct / 40, 0, 1) })
  }
  if (parts.length === 0) return null
  const totalWeight = parts.reduce((sum, p) => sum + p.weight, 0)
  return (100 * parts.reduce((sum, p) => sum + p.weight * p.value, 0)) / totalWeight
}

/** One stored vitals row (Data's `vitals` table), time in epoch ms. */
export interface SimReading {
  time: number
  heart_rate: number | null
  breathing_rate: number | null
  expression: Expression | null
  confidence: number
  stress: number | null
}

export const BASELINE_WARMUP_MS = 10_000
export const BASELINE_MIN_SAMPLES = 10
export const POPULATION_BASELINE: BaselineValues = { heart_rate: 75, breathing_rate: 15 }

export interface BaselineResult {
  baseline: Baseline
  /** True when population defaults were used (too few good samples). */
  fallback: boolean
}

/**
 * data.md compute_baseline: average of readings with confidence >= 0.5 from
 * session start until `untilMs`, skipping the first 10 s (sensor warm-up).
 * Fewer than 10 samples falls back to population defaults.
 */
export function computeBaseline(
  readings: readonly SimReading[],
  sessionStartMs: number,
  untilMs: number,
): BaselineResult {
  const usable = readings.filter(
    (r) => r.time >= sessionStartMs + BASELINE_WARMUP_MS && r.time <= untilMs && r.confidence >= MIN_CONFIDENCE,
  )
  if (usable.length < BASELINE_MIN_SAMPLES) {
    return { baseline: { ...POPULATION_BASELINE, n_samples: usable.length }, fallback: true }
  }
  const hr = mean(usable.map((r) => r.heart_rate))
  const br = mean(usable.map((r) => r.breathing_rate))
  return {
    baseline: {
      heart_rate: round(hr ?? POPULATION_BASELINE.heart_rate, 1),
      breathing_rate: round(br ?? POPULATION_BASELINE.breathing_rate, 1),
      n_samples: usable.length,
    },
    fallback: hr === null || br === null,
  }
}

// ---------------------------------------------------------------------------
// Vitals model (mirrors scripts/mock_vitals.py: s = spike, d = mild, f = face lost)
// ---------------------------------------------------------------------------

export type OverlayKind = 'spike' | 'mild' | 'face_lost'

interface OverlaySpec {
  rampMs: number
  holdMs: number
  decayMs: number
  /** Fractional increase at full strength. */
  hr: number
  br: number
  expression: Expression | null
}

const OVERLAYS: Record<Exclude<OverlayKind, 'face_lost'>, OverlaySpec> = {
  // holdMs is 10 s on purpose: Brain's watcher flags 'high' on a 10 s *mean* above 70, so a
  // shorter peak (stress ~85 for 4 s) never turns the toast red in the demo.
  spike: { rampMs: 6_000, holdMs: 10_000, decayMs: 20_000, hr: 0.28, br: 0.3, expression: 'frown' },
  mild: { rampMs: 4_000, holdMs: 3_000, decayMs: 15_000, hr: 0.12, br: 0.15, expression: null },
}

export const FACE_LOST_MS = 4_000

/** 0..1 strength of an overlay `dt` ms after it started. */
function envelope(spec: OverlaySpec, dt: number): number {
  if (dt < 0) return 0
  if (dt < spec.rampMs) return smoothstep(dt / spec.rampMs)
  if (dt < spec.rampMs + spec.holdMs) return 1
  const decayed = dt - spec.rampMs - spec.holdMs
  if (decayed < spec.decayMs) return 1 - smoothstep(decayed / spec.decayMs)
  return 0
}

/** Raw (unrounded) sensor sample. */
export interface VitalsSample {
  heart_rate: number | null
  breathing_rate: number | null
  expression: Expression | null
  confidence: number
}

// Ornstein-Uhlenbeck walks: x = 0.85 x + sigma * N(0, 1). Stationary std = sigma / sqrt(1 - 0.85^2).
const WALK_DECAY = 0.85
const WALK_SCALE = Math.sqrt(1 - WALK_DECAY * WALK_DECAY)

/**
 * One reading per call (call it once per second, in time order: the noise is a
 * smooth random walk, so the model is stateful). Overlays are anchored to
 * absolute times and can overlap.
 */
export class VitalsModel {
  readonly baseHeartRate: number
  readonly baseBreathingRate: number
  private readonly rng: Rng
  private hrNoise = 0
  private brNoise = 0
  private readonly overlays: { kind: OverlayKind; startMs: number }[] = []

  constructor(rng: Rng) {
    this.rng = rng
    this.baseHeartRate = 72 + (rng() - 0.5) * 4
    this.baseBreathingRate = 14 + (rng() - 0.5) * 1.2
    rng() // the retired focus vital's draw: kept so seeded mock streams (and the fixtures) stay identical
  }

  addOverlay(kind: OverlayKind, startMs: number): void {
    this.overlays.push({ kind, startMs })
  }

  sample(timeMs: number): VitalsSample {
    const rng = this.rng
    // Always advance the walks (even while the face is lost) so the stream stays deterministic.
    this.hrNoise = WALK_DECAY * this.hrNoise + 1.5 * WALK_SCALE * gaussian(rng)
    this.brNoise = WALK_DECAY * this.brNoise + 0.6 * WALK_SCALE * gaussian(rng)
    gaussian(rng) // the retired focus walk's draw, kept for the same reason as in the constructor
    const confidence = 0.93 + rng() * 0.04

    let lost = false
    let hrBoost = 0
    let brBoost = 0
    let expression: Expression = 'neutral'
    for (const overlay of this.overlays) {
      const dt = timeMs - overlay.startMs
      if (overlay.kind === 'face_lost') {
        if (dt >= 0 && dt < FACE_LOST_MS) lost = true
        continue
      }
      const spec = OVERLAYS[overlay.kind]
      const e = envelope(spec, dt)
      if (e <= 0) continue
      hrBoost += e * spec.hr
      brBoost += e * spec.br
      if (spec.expression && e > 0.4) expression = spec.expression
    }

    if (lost) return { heart_rate: null, breathing_rate: null, expression: null, confidence: 0 }

    return {
      heart_rate: (this.baseHeartRate + this.hrNoise) * (1 + hrBoost),
      breathing_rate: Math.max(4, (this.baseBreathingRate + this.brNoise) * (1 + brBoost)),
      expression,
      confidence,
    }
  }
}

// ---------------------------------------------------------------------------
// Window stats + Brain's watcher
// ---------------------------------------------------------------------------

/** data.md get_window_stats over (now - seconds, now], confidence >= 0.5. */
export function windowStats(
  readings: readonly SimReading[],
  nowMs: number,
  seconds: number,
  baseline: BaselineValues | null,
): WindowStats {
  const from = nowMs - seconds * 1000
  const rows = readings.filter((r) => r.time > from && r.time <= nowMs && r.confidence >= MIN_CONFIDENCE)
  const hr = mean(rows.map((r) => r.heart_rate))
  const br = mean(rows.map((r) => r.breathing_rate))
  const counts = new Map<string, number>()
  for (const r of rows) if (r.expression) counts.set(r.expression, (counts.get(r.expression) ?? 0) + 1)
  let dominant: string | null = null
  for (const [expr, n] of counts) if (dominant === null || n > (counts.get(dominant) ?? 0)) dominant = expr
  return {
    seconds,
    n_samples: rows.length,
    hr_mean: hr,
    hr_delta_pct: hr !== null && baseline ? ((hr - baseline.heart_rate) / baseline.heart_rate) * 100 : null,
    br_mean: br,
    br_delta_pct: br !== null && baseline ? ((br - baseline.breathing_rate) / baseline.breathing_rate) * 100 : null,
    dominant_expression: dominant,
    stress: mean(rows.map((r) => r.stress)),
  }
}

/** Brain's exact rule: > 70 high, > 40 rising, else nothing. */
export function watcherLevel(stress: number | null): CoachLevel | null {
  if (stress === null) return null
  return stress > 70 ? 'high' : stress > 40 ? 'rising' : null
}

/**
 * Mirrors backend/brain/watcher.py: Judge names the signal driving the stress, cites its number,
 * and gives one command the candidate must obey before the interview moves on. `used` carries the phrasings
 * already shown so none repeats (the frontend swallows duplicates, so a repeat would be a dropped flag).
 */
const FLAG_VARIANTS: Record<string, { message: (n: number) => string; command: string }[]> = {
  'hr:rising': [
    { message: (n) => `Your pulse climbs ${n}% above your own calm. I have noticed.`,
      command: 'slow your pace and finish the sentence you started' },
    { message: (n) => `${n}% over your baseline. Your heart answers faster than your mouth.`,
      command: 'take one slow breath before your next point' },
  ],
  'hr:high': [
    { message: (n) => `Your heart races ${n}% past your baseline. Steady yourself before the court.`,
      command: 'stop, breathe out slowly, then continue' },
    { message: (n) => `A pulse ${n}% above your calm. This stand has rattled steadier witnesses.`,
      command: 'pause for two full seconds before you say another word' },
  ],
  'br:rising': [
    { message: (n) => `Your breath shortens, ${n}% quicker than your calm.`,
      command: 'let one slow breath out before continuing' },
  ],
  'br:high': [
    { message: (n) => `Your breathing runs ${n}% above your calm. You are rushing.`,
      command: 'stop speaking and take one long breath out' },
  ],
  'none:rising': [
    { message: (n) => `Your composure sits at ${n} out of a hundred.`, command: 'slow down and take one steady breath' },
  ],
  'none:high': [
    { message: (n) => `Your composure has broken to ${n} out of a hundred.`,
      command: 'stop, breathe, and begin that answer again' },
  ],
}

export function flagFor(
  w: WindowStats,
  level: CoachLevel,
  used: Set<string> = new Set(),
): { message: string; command: string } {
  const hrPart = w.hr_delta_pct === null ? 0 : 0.5 * clamp(w.hr_delta_pct / 25, 0, 1)
  const brPart = w.br_delta_pct === null ? 0 : 0.3 * clamp(w.br_delta_pct / 40, 0, 1)

  let driver = 'none'
  let value = Math.round(w.stress ?? 0)
  if (w.br_delta_pct !== null && brPart > hrPart) {
    driver = 'br'
    value = Math.round(w.br_delta_pct)
  } else if (w.hr_delta_pct !== null && hrPart > 0) {
    driver = 'hr'
    value = Math.round(w.hr_delta_pct)
  }

  const pools = [FLAG_VARIANTS[`${driver}:${level}`] ?? [], FLAG_VARIANTS[`none:${level}`] ?? []]
  for (const pool of pools) {
    for (const variant of pool) {
      const message = variant.message(value)
      if (!used.has(message)) {
        used.add(message)
        return { message, command: variant.command }
      }
    }
  }
  const fallback = pools.flat()[0]
  return fallback
    ? { message: fallback.message(value), command: fallback.command }
    : { message: 'I am watching you closely.', command: 'take one steady breath' }
}
