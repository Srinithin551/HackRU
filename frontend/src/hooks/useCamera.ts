/**
 * The webcam preview on the live screen. Video only (the mic is used by speech-to-text) and nothing is
 * recorded or uploaded: the stream only feeds a <video> element.
 *
 * The stream is the app's one shared camera stream (lib/cameraStream.ts), opened when the app loads and kept
 * until the window closes, because Presage measures from the same camera and re-opening it could change the
 * camera's mode under Presage. So "Hide camera" only hides the video; it doesn't release the camera.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { hasCamera, PREVIEW_ENABLED, sharedCamera } from '../lib/cameraStream.ts'

export type CameraStatus = 'starting' | 'live' | 'denied' | 'unavailable' | 'off'

export function useCamera() {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  // VITE_CAMERA_PREVIEW=0 starts with the preview off (run_demo.py --no-preview), leaving the webcam to Presage.
  const [enabled, setEnabled] = useState(PREVIEW_ENABLED)
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<Exclude<CameraStatus, 'off'>>(hasCamera() ? 'starting' : 'unavailable')

  useEffect(() => {
    if (!enabled || !hasCamera()) return
    const video = videoRef.current // the <video> is always rendered, so it exists by now
    let cancelled = false
    sharedCamera()
      .then((stream) => {
        if (cancelled) return
        if (video) {
          video.srcObject = stream
          video.play().catch(() => {})
        }
        setStatus('live')
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setStatus(err instanceof DOMException && err.name === 'NotAllowedError' ? 'denied' : 'unavailable')
      })
    return () => {
      cancelled = true
      if (video) video.srcObject = null // detach only: the shared stream (and the camera) stays open
    }
  }, [enabled, attempt])

  const toggle = useCallback(() => {
    setEnabled((on) => !on)
    setStatus('starting')
  }, [])

  const retry = useCallback(() => {
    setStatus('starting')
    setAttempt((n) => n + 1)
  }, [])

  return { videoRef, status: (enabled ? status : 'off') as CameraStatus, enabled, toggle, retry }
}
