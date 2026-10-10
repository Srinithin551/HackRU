/**
 * Smoothly animates toward a target number (ease-out), so the heart rate and stress
 * gauge glide between 1 Hz readings instead of jumping: it sells "live".
 * Honors prefers-reduced-motion by snapping. Null passes straight through.
 */
import { useEffect, useRef, useState } from 'react'
import { motion } from '../styles/tokens.ts'

const reduceMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function useTweenedNumber(target: number | null, durationMs = motion.tweenMs): number | null {
  const [shown, setShown] = useState<number | null>(target)
  const shownRef = useRef<number | null>(target)

  useEffect(() => {
    const from = shownRef.current
    let frame = 0
    const set = (v: number | null) => {
      shownRef.current = v
      setShown(v)
    }
    if (target === null || from === null || reduceMotion()) {
      frame = requestAnimationFrame(() => set(target))
      return () => cancelAnimationFrame(frame)
    }
    const startedAt = performance.now()
    const step = (now: number) => {
      const p = Math.min(1, (now - startedAt) / durationMs)
      const eased = 1 - (1 - p) ** 3
      set(from + (target - from) * eased)
      if (p < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [target, durationMs])

  return shown
}
