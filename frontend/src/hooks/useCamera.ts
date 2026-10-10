/**
 * The webcam preview in the middle of the live screen. Video only (the mic is used
 * by speech-to-text) and nothing is recorded or uploaded: the stream only feeds a
 * <video> element.
 *
 * Note for Sensing: if Presage runs as a separate local process (Option B in
 * sensing.md), it may need exclusive access to the camera on macOS. Then turn the
 * preview off with the camera button; vitals keep flowing from the sensor.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export type CameraStatus = 'starting' | 'live' | 'denied' | 'unavailable' | 'off'

const hasCamera = () => typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia

export function useCamera() {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [enabled, setEnabled] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<Exclude<CameraStatus, 'off'>>(hasCamera() ? 'starting' : 'unavailable')

  useEffect(() => {
    if (!enabled || !hasCamera()) return
    const video = videoRef.current // the <video> is always rendered, so it exists by now
    let stream: MediaStream | null = null
    let cancelled = false
    navigator.mediaDevices
      .getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' }, audio: false })
      .then((s) => {
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop())
          return
        }
        stream = s
        if (video) {
          video.srcObject = s
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
      stream?.getTracks().forEach((t) => t.stop()) // turns the camera light off
      if (video) video.srcObject = null
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
