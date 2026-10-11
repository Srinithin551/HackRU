/** Pieces drawn on or next to the camera: voice waveform, coach toast, warm-up ring, Presage hints. */
import { CALIBRATION_MS } from '../lib/constants.ts'
import { STRESS_META } from '../styles/tokens.ts'
import type { CoachFlagEvent, SensorStatusEvent } from '../types.ts'
import { StressIcon } from './ui.tsx'

const BARS = [0.55, 0.85, 1, 0.7, 0.9, 0.6, 0.8]

/** Interviewer voice bars: bounce while speaking, rest flat otherwise. */
export function Waveform({ active }: { active: boolean }) {
  return (
    <div className="flex h-7 items-center gap-[3px]" aria-hidden="true">
      {BARS.map((h, i) => (
        <span
          key={i}
          className={`w-[3px] origin-center rounded-full ${active ? 'animate-wave bg-accent' : 'bg-ink-disabled'}`}
          style={{ height: `${h * 100}%`, animationDelay: `${i * 0.11}s`, transform: active ? undefined : 'scaleY(0.2)' }}
        />
      ))}
    </div>
  )
}

/** "…" while Brain asks Gemini what to do next. */
export function ThinkingDots() {
  return (
    <span className="inline-flex gap-1" aria-label="thinking">
      {[0, 1, 2].map((i) => (
        <span key={i} className="size-1.5 animate-thinking rounded-full bg-ink-secondary" style={{ animationDelay: `${i * 0.15}s` }} />
      ))}
    </span>
  )
}

/** Coach flag banner (amber "rising" / red "high"), icon + word + message so it never relies on color. */
export function CoachToast({ flag }: { flag: CoachFlagEvent }) {
  const meta = STRESS_META[flag.level]
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex max-w-xl animate-toast-in items-start gap-3 rounded-control border px-4 py-3 shadow-[0_12px_40px_rgb(0_0_0/0.45)] backdrop-blur-md"
      style={{ backgroundColor: `${meta.subtle}e6`, borderColor: meta.color }}
    >
      <StressIcon level={flag.level} className="mt-1 size-3.5 shrink-0" />
      <div>
        <p className="font-mono text-micro uppercase tracking-[0.08em] text-ink">
          Coach · {flag.level === 'high' ? 'High stress' : 'Stress rising'}
        </p>
        <p className="mt-0.5 text-body font-medium text-ink">{flag.message}</p>
      </div>
    </div>
  )
}

/** Warm-up banner with a ring that fills over Brain's minimum calibration time. */
export function CalibrationBanner({ startedAt, now }: { startedAt: number | null; now: number }) {
  const elapsed = startedAt === null ? 0 : now - startedAt
  const progress = Math.min(1, elapsed / CALIBRATION_MS)
  const r = 15
  const c = 2 * Math.PI * r
  return (
    <div className="flex animate-toast-in items-center gap-3 rounded-control border border-hairline bg-surface-1/85 px-4 py-2.5 backdrop-blur-md">
      <svg viewBox="0 0 36 36" className="size-9 -rotate-90" aria-hidden="true">
        <circle cx="18" cy="18" r={r} fill="none" strokeWidth="3" className="stroke-surface-3" />
        <circle
          cx="18"
          cy="18"
          r={r}
          fill="none"
          strokeWidth="3"
          strokeLinecap="round"
          className="stroke-accent transition-[stroke-dashoffset] duration-1000 ease-linear"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - progress)}
        />
      </svg>
      <div>
        <p className="text-body font-medium">Getting your baseline. Just chat normally.</p>
        <p className="text-caption text-ink-muted">
          {progress < 1 ? `About ${Math.ceil((CALIBRATION_MS - elapsed) / 1000)} s of warm-up left` : 'Almost ready'}
        </p>
      </div>
    </div>
  )
}

/**
 * What Presage needs from the user ("Hold still.", "No face found…"), from the hidden sensor. Amber for
 * framing/lighting, red when Presage itself failed, neutral while it starts. Nothing while measuring normally.
 */
export function SensorHint({ sensor }: { sensor: SensorStatusEvent }) {
  if (!sensor.message || sensor.state === 'measuring' || sensor.state === 'idle') return null
  const tone =
    sensor.state === 'error'
      ? 'border-high bg-high-subtle/90'
      : sensor.state === 'adjust'
        ? 'border-rising bg-rising-subtle/90'
        : 'border-hairline bg-surface-1/85'
  return (
    <div role="status" aria-live="polite" className={`max-w-md rounded-control border px-4 py-2 text-center backdrop-blur-md ${tone}`}>
      <p className="font-mono text-micro uppercase tracking-[0.08em] text-ink-muted">Presage</p>
      <p className="text-caption font-medium text-ink">{sensor.message}</p>
    </div>
  )
}

/** Shown during a --simulate test so nobody mistakes the vitals for real readings. */
export function SimulatedChip() {
  return (
    <span className="rounded-full border border-rising bg-page/60 px-3 py-1 font-mono text-micro uppercase tracking-[0.08em] text-ink-secondary backdrop-blur">
      Simulated vitals
    </span>
  )
}
