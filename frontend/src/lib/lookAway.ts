/**
 * Look-away detection: the pure rules behind hooks/useLookAway.ts (which runs MediaPipe's face
 * landmarker on the camera). DOM-free so it can be unit-tested.
 *
 * Each camera frame becomes a Gaze (head yaw/pitch in degrees, how far the eyes are turned, or null
 * when no face is in view). `isAway` says whether that frame is looking off the screen, and
 * `LookAwayTracker` turns frames into look-away episodes: away for LOOK_AWAY_MIN_MS counts once.
 * Whether several episodes add up to "suspected of cheating" is the backend's call
 * (backend/brain/integrity.py), so the threshold lives in one place.
 */

export interface Gaze {
  /** Head turned left/right from facing the camera, degrees (sign ignored). */
  yaw: number
  /** Head tilted up/down, degrees (sign ignored). */
  pitch: number
  /** 0 to 1: eyes turned sideways, from MediaPipe's eyeLookIn/eyeLookOut blendshapes. */
  eyesSide: number
  /** 0 to 1: eyes turned down (a phone or notes in the lap), from eyeLookDown. */
  eyesDown: number
}

/** Head turned further than this = not looking at the screen. */
export const MAX_YAW_DEG = 28
/** Generous, because a camera above the screen already sees the head tilted down a little. */
export const MAX_PITCH_DEG = 25
/** Eyes alone: a sideways or downward look this strong is off the screen. */
export const MAX_EYES = 0.6

/** Away this long = one look-away. Shorter glances are thinking, not reading. */
export const LOOK_AWAY_MIN_MS = 1_500
/** Back on screen this long ends the episode; a flicker of detector noise doesn't split it in two. */
export const RETURN_MS = 600

/** No face counts as away: the candidate turned fully or left the frame. */
export function isAway(gaze: Gaze | null): boolean {
  if (gaze === null) return true
  return (
    Math.abs(gaze.yaw) > MAX_YAW_DEG ||
    Math.abs(gaze.pitch) > MAX_PITCH_DEG ||
    gaze.eyesSide > MAX_EYES ||
    gaze.eyesDown > MAX_EYES
  )
}

/**
 * Yaw and pitch from MediaPipe's 4x4 facial transformation matrix (16 numbers). The signs are thrown
 * away, which also makes the result the same whether the matrix is stored row- or column-major: for a
 * rotation the two readings differ only in sign.
 */
export function headAngles(m: ArrayLike<number>): { yaw: number; pitch: number } {
  const deg = 180 / Math.PI
  const clamp = (v: number) => Math.max(-1, Math.min(1, v))
  return {
    yaw: Math.abs(Math.asin(clamp(m[2])) * deg),
    pitch: Math.abs(Math.atan2(m[6], m[10]) * deg),
  }
}

/** Eye direction from MediaPipe's blendshape scores (keyed by categoryName). */
export function eyeScores(score: (name: string) => number): { eyesSide: number; eyesDown: number } {
  // Looking to one side turns one eye "out" and the other "in".
  const toLeft = (score('eyeLookOutLeft') + score('eyeLookInRight')) / 2
  const toRight = (score('eyeLookInLeft') + score('eyeLookOutRight')) / 2
  return {
    eyesSide: Math.max(toLeft, toRight),
    eyesDown: (score('eyeLookDownLeft') + score('eyeLookDownRight')) / 2,
  }
}

/** Frames in, look-away episodes out. */
export class LookAwayTracker {
  private awaySince: number | null = null
  private backSince: number | null = null
  private reported = false

  /** One frame at time `t` (ms). True exactly once per episode, when it has lasted LOOK_AWAY_MIN_MS. */
  update(t: number, away: boolean): boolean {
    if (away) {
      this.backSince = null
      this.awaySince ??= t
      if (!this.reported && t - this.awaySince >= LOOK_AWAY_MIN_MS) {
        this.reported = true
        return true
      }
      return false
    }
    if (this.awaySince === null) return false
    this.backSince ??= t
    if (t - this.backSince >= RETURN_MS) this.reset()
    return false
  }

  reset(): void {
    this.awaySince = null
    this.backSince = null
    this.reported = false
  }
}
