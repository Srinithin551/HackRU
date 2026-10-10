/** Small shared building blocks: brand mark, buttons, stress icon/badge, status dot. */
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { STRESS_META, stressLevel, type StressLevel } from '../styles/tokens.ts'

/** Logo: an ECG pulse in the accent color + wordmark. */
export function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-8" aria-hidden="true">
        <rect width="32" height="32" rx="9" className="fill-accent-subtle" />
        <path
          d="M5 17h5l2.5-6 4 11 3-8 2 3H27"
          fill="none"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="stroke-accent"
        />
      </svg>
      <span className="text-lead font-semibold tracking-tight">StressTest</span>
    </div>
  )
}

type Variant = 'primary' | 'secondary' | 'ghost'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-on-accent hover:bg-accent-hover active:bg-accent-pressed disabled:bg-surface-3 disabled:text-ink-disabled',
  secondary: 'border border-border-strong bg-surface-2 text-ink hover:bg-surface-3 disabled:text-ink-disabled',
  ghost: 'text-ink-secondary hover:bg-surface-2 hover:text-ink disabled:text-ink-disabled',
}

export function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 rounded-control px-4 py-2.5 text-body font-medium transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  )
}

/** Shape carries the level too (circle / triangle / diamond), so it reads without color. */
export function StressIcon({ level, className = 'size-3' }: { level: StressLevel; className?: string }) {
  const { color, icon } = STRESS_META[level]
  return (
    <svg viewBox="0 0 12 12" className={className} aria-hidden="true">
      {icon === 'circle' && <circle cx="6" cy="6" r="5" fill={color} />}
      {icon === 'triangle' && <path d="M6 1 11 10.5H1Z" fill={color} />}
      {icon === 'diamond' && <path d="M6 0.5 11.5 6 6 11.5 0.5 6Z" fill={color} />}
    </svg>
  )
}

/** "● Calm" / "▲ Rising" / "◆ High" for a stress score; `empty` when the score is null. */
export function StressBadge({ score, empty = 'No reading' }: { score: number | null | undefined; empty?: string }) {
  const level = stressLevel(score)
  if (!level) return <span className="label-caps">{empty}</span>
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-micro font-medium uppercase tracking-[0.08em] text-ink">
      <StressIcon level={level} />
      {STRESS_META[level].label}
    </span>
  )
}

/** Small live/idle indicator: pulsing dot + text. */
export function StatusDot({ on, children }: { on: boolean; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 text-caption text-ink-secondary">
      <span className="relative flex size-2.5">
        {on && <span className="absolute inset-0 animate-ping rounded-full bg-accent opacity-60" />}
        <span className={`relative size-2.5 rounded-full ${on ? 'bg-accent' : 'bg-ink-disabled'}`} />
      </span>
      {children}
    </span>
  )
}

/** Full-screen "Analyzing your session…" shown between the interview and the report. */
export function AnalyzingScreen({ detail }: { detail?: string }) {
  return (
    <div className="bg-monitor fixed inset-0 z-50 flex animate-fade-in flex-col items-center justify-center gap-6 p-gutter text-center">
      <div className="relative h-16 w-64 overflow-hidden" aria-hidden="true">
        <svg viewBox="0 0 256 64" className="size-full">
          <path
            d="M0 34h70l10-18 14 36 12-30 8 12h142"
            fill="none"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="stroke-accent"
          />
        </svg>
        <div className="absolute inset-y-0 w-1/3 animate-sweep bg-[linear-gradient(90deg,transparent,var(--color-page)_60%)]" />
      </div>
      <div>
        <p className="text-title font-semibold">Analyzing your session…</p>
        <p className="mt-2 text-body text-ink-secondary">
          {detail ?? 'Lining up every answer with your heart rate and stress.'}
        </p>
      </div>
    </div>
  )
}
