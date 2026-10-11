/** Small shared building blocks: brand mark, buttons, stress icon/badge, status dot. */
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { STRESS_META, stressLevel, type StressLevel } from '../styles/tokens.ts'

/** Logo: a gold temple front on pale stone + wordmark. */
export function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden="true">
        <rect width="32" height="32" rx="9" className="fill-gold-subtle" />
        {/* pediment */}
        <path d="M16 6 26 13H6Z" className="fill-gold" />
        {/* architrave */}
        <rect x="7" y="14.5" width="18" height="2" rx="0.6" className="fill-gold" />
        {/* three columns */}
        <rect x="9.5" y="17.5" width="2.4" height="7" rx="0.8" className="fill-gold" />
        <rect x="14.8" y="17.5" width="2.4" height="7" rx="0.8" className="fill-gold" />
        <rect x="20.1" y="17.5" width="2.4" height="7" rx="0.8" className="fill-gold" />
        {/* stylobate */}
        <rect x="7" y="25.5" width="18" height="2" rx="0.6" className="fill-gold" />
      </svg>
      <span className="flex flex-col leading-none">
        <span className="text-gilded font-mono text-micro uppercase tracking-[0.18em]">Greek God</span>
        <span className="text-lead font-semibold tracking-tight">Teacher Program</span>
      </span>
    </div>
  )
}

/** A thin gold inlay rule, used under section headings. */
export function GoldRule({ className = '' }: { className?: string }) {
  return <div className={`rule-gold h-px w-full ${className}`} aria-hidden="true" />
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

/** Full-screen "The gods confer…" shown between the trial and the verdict. */
export function AnalyzingScreen({ detail }: { detail?: string }) {
  return (
    <div className="bg-monitor fixed inset-0 z-50 flex animate-fade-in flex-col items-center justify-center gap-6 p-gutter text-center">
      {/* Three pale figures deliberating, their auras breathing out of phase. */}
      <div className="flex items-end gap-5" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="relative flex size-16 items-center justify-center">
            <span
              className="absolute inset-0 animate-aura rounded-full bg-pale blur-md"
              style={{ animationDelay: `${i * 0.6}s` }}
            />
            <svg viewBox="0 0 32 48" className="relative h-14">
              {/* a simple pale statue: head and draped shoulders */}
              <circle cx="16" cy="11" r="6.5" className="fill-pale-deep" />
              <path d="M16 19c-7 0-11 5-12 13h24c-1-8-5-13-12-13Z" className="fill-pale-deep" />
            </svg>
          </div>
        ))}
      </div>
      <div>
        <p className="text-title font-semibold">The gods confer…</p>
        <p className="mt-2 text-body text-ink-secondary">
          {detail ?? 'Weighing every answer against what your body betrayed.'}
        </p>
      </div>
    </div>
  )
}
