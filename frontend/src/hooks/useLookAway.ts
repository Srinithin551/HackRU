/**
 * Watches where the candidate is looking while they answer, and calls `onLookAway` each time they have
 * looked off the screen for a while (lib/lookAway.ts has the rules). The backend counts these and only
 * repeated ones become "suspected of cheating" (backend/brain/integrity.py).
 *
 * MediaPipe's face landmarker runs in the browser on the app's shared camera stream
 * (lib/cameraStream.ts), so no video leaves the machine. It reads its own hidden <video>, so hiding the
 * preview doesn't stop it. It's loaded from the CDN the first time an answer starts; if that fails
 * (offline) or the page never opened the camera (VITE_CAMERA_PREVIEW=0), nothing is watched and the
 * interview carries on as before.
 */
import { useEffect, useRef } from 'react'
import { hasCamera, PREVIEW_ENABLED, sharedCamera } from '../lib/cameraStream.ts'
import { eyeScores, headAngles, isAway, LookAwayTracker, type Gaze } from '../lib/lookAway.ts'

const MEDIAPIPE = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21'
const MODEL =
  'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'
/** About 5 frames a second: plenty for a look-away that has to last 1.5 s, and light on the laptop. */
const FRAME_MS = 200

/** The slice of @mediapipe/tasks-vision this hook uses (loaded at runtime, so typed by hand). */
interface FaceLandmarkerResult {
  faceLandmarks: unknown[]
  faceBlendshapes?: { categories: { categoryName: string; score: number }[] }[]
  facialTransformationMatrixes?: { data: number[] }[]
}
interface FaceLandmarker {
  detectForVideo(video: HTMLVideoElement, timestampMs: number): FaceLandmarkerResult
}

let landmarker: Promise<FaceLandmarker> | null = null

/** One landmarker for the page, created on first use. A failed load can be retried on the next answer. */
function loadLandmarker(): Promise<FaceLandmarker> {
  if (landmarker) return landmarker
  const loading = (async () => {
    const vision = await import(/* @vite-ignore */ `${MEDIAPIPE}/vision_bundle.mjs`)
    const fileset = await vision.FilesetResolver.forVisionTasks(`${MEDIAPIPE}/wasm`)
    return (await vision.FaceLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: MODEL, delegate: 'GPU' },
      runningMode: 'VIDEO',
      numFaces: 1,
      outputFaceBlendshapes: true,
      outputFacialTransformationMatrixes: true,
    })) as FaceLandmarker
  })().catch((err: unknown) => {
    landmarker = null
    throw err
  })
  landmarker = loading
  return loading
}

/** Where this frame is looking, or null when there's no face in it. */
function gazeOf(result: FaceLandmarkerResult): Gaze | null {
  const matrix = result.facialTransformationMatrixes?.[0]?.data
  if (!result.faceLandmarks.length || !matrix) return null
  const scores = new Map<string, number>(
    result.faceBlendshapes?.[0]?.categories.map((c): [string, number] => [c.categoryName, c.score]) ?? [],
  )
  return { ...headAngles(matrix), ...eyeScores((name) => scores.get(name) ?? 0) }
}

/** `active`: the candidate is answering. Nothing is watched otherwise. */
export function useLookAway(active: boolean, onLookAway: () => void): void {
  const onLookAwayRef = useRef(onLookAway)
  useEffect(() => {
    onLookAwayRef.current = onLookAway
  })

  useEffect(() => {
    if (!active || !PREVIEW_ENABLED || !hasCamera()) return
    let cancelled = false
    let timer: ReturnType<typeof setInterval> | undefined
    const video = document.createElement('video')
    video.muted = true
    video.playsInline = true
    const tracker = new LookAwayTracker()

    Promise.all([loadLandmarker(), sharedCamera()])
      .then(([faces, stream]) => {
        if (cancelled) return
        video.srcObject = stream
        video.play().catch(() => {})
        let lastTs = 0
        timer = setInterval(() => {
          if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return
          const now = performance.now()
          if (now <= lastTs) return // MediaPipe needs strictly increasing timestamps
          lastTs = now
          try {
            if (tracker.update(now, isAway(gazeOf(faces.detectForVideo(video, now))))) onLookAwayRef.current()
          } catch (err) {
            console.warn('[look-away] detection failed, stopping for this answer', err)
            clearInterval(timer)
          }
        }, FRAME_MS)
      })
      .catch((err: unknown) => {
        if (!cancelled) console.warn('[look-away] face landmarker unavailable; look-aways are not watched', err)
      })

    return () => {
      cancelled = true
      clearInterval(timer)
      video.pause()
      video.srcObject = null // detach only: the shared stream (and the camera) stays open
    }
  }, [active])
}
