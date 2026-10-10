/**
 * Clock + timers abstraction for the mock backend. The live mock uses real time;
 * tests and scripts/gen-fixtures.ts drive a VirtualScheduler so a whole session
 * runs in milliseconds and is byte-for-byte reproducible.
 *
 * DOM-free: only ES2023 plus the global timer functions.
 */

export type TimerId = number

export interface Scheduler {
  /** Epoch milliseconds. */
  now(): number
  setTimeout(fn: () => void, ms: number): TimerId
  setInterval(fn: () => void, ms: number): TimerId
  clear(id: TimerId): void
}

type RealHandle =
  | { kind: 'timeout'; handle: ReturnType<typeof setTimeout> }
  | { kind: 'interval'; handle: ReturnType<typeof setInterval> }

/** Wall clock + the global timers. Ids are plain numbers on every platform. */
export function createRealScheduler(): Scheduler {
  let nextId = 1
  const handles = new Map<TimerId, RealHandle>()
  return {
    now: () => Date.now(),
    setTimeout(fn, ms) {
      const id = nextId++
      const handle = setTimeout(() => {
        handles.delete(id)
        fn()
      }, ms)
      handles.set(id, { kind: 'timeout', handle })
      return id
    },
    setInterval(fn, ms) {
      const id = nextId++
      handles.set(id, { kind: 'interval', handle: setInterval(fn, ms) })
      return id
    },
    clear(id) {
      const entry = handles.get(id)
      if (!entry) return
      handles.delete(id)
      if (entry.kind === 'timeout') clearTimeout(entry.handle)
      else clearInterval(entry.handle)
    },
  }
}

/** Shared real-time scheduler (the default for the in-browser mock). */
export const realScheduler: Scheduler = createRealScheduler()

interface VirtualTimer {
  id: TimerId
  at: number
  /** Insertion order, so equal-time timers fire first-in first-out. */
  seq: number
  fn: () => void
  every: number | null
}

/** Lets pending promise chains (async/await driven by timers) settle. */
const flushMicrotasks = () => new Promise<void>((resolve) => setTimeout(resolve, 0))

/**
 * Deterministic virtual clock. Nothing fires on its own: call advance() or
 * runUntil(). Timers fire in (time, insertion) order and pending microtasks are
 * flushed after every callback.
 */
export class VirtualScheduler implements Scheduler {
  private time: number
  private nextId = 1
  private seq = 0
  private readonly timers = new Map<TimerId, VirtualTimer>()

  constructor(startMs: number) {
    this.time = startMs
  }

  now(): number {
    return this.time
  }

  setTimeout(fn: () => void, ms: number): TimerId {
    return this.add(fn, ms, null)
  }

  setInterval(fn: () => void, ms: number): TimerId {
    return this.add(fn, ms, Math.max(1, ms))
  }

  clear(id: TimerId): void {
    this.timers.delete(id)
  }

  /** Number of pending timers (handy in tests to check stop() cleaned up). */
  get pending(): number {
    return this.timers.size
  }

  /** Moves the clock forward by `ms`, firing every timer due on the way. */
  async advance(ms: number): Promise<void> {
    const target = this.time + Math.max(0, ms)
    for (;;) {
      const next = this.peek()
      if (!next || next.at > target) break
      await this.fire(next)
    }
    this.time = target
    await flushMicrotasks()
  }

  /**
   * Fires timers in order until `predicate()` holds or `maxMs` of virtual time
   * has passed. Resolves to whether the predicate held.
   */
  async runUntil(predicate: () => boolean, maxMs: number): Promise<boolean> {
    const limit = this.time + Math.max(0, maxMs)
    await flushMicrotasks()
    while (!predicate()) {
      const next = this.peek()
      if (!next || next.at > limit) {
        this.time = limit
        await flushMicrotasks()
        return predicate()
      }
      await this.fire(next)
    }
    return true
  }

  private add(fn: () => void, ms: number, every: number | null): TimerId {
    const id = this.nextId++
    const delay = Math.max(0, Number.isFinite(ms) ? ms : 0)
    this.timers.set(id, { id, at: this.time + (every ?? delay), seq: this.seq++, fn, every })
    return id
  }

  private peek(): VirtualTimer | undefined {
    let best: VirtualTimer | undefined
    for (const timer of this.timers.values()) {
      if (!best || timer.at < best.at || (timer.at === best.at && timer.seq < best.seq)) best = timer
    }
    return best
  }

  private async fire(timer: VirtualTimer): Promise<void> {
    this.time = Math.max(this.time, timer.at)
    if (timer.every === null) {
      this.timers.delete(timer.id)
    } else {
      // Re-queue before running so the callback may clear its own interval.
      timer.at += timer.every
      timer.seq = this.seq++
    }
    try {
      timer.fn()
    } finally {
      await flushMicrotasks()
    }
  }
}
