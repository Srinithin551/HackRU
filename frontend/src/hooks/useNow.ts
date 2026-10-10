import { useEffect, useState } from 'react'

/** The current time, re-rendering every `intervalMs` (for "sensor connected" and countdowns). */
export function useNow(intervalMs = 1_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}
