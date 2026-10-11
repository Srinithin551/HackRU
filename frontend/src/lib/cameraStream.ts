/**
 * One webcam stream for the whole app: opened once, as early as possible, and never re-opened.
 *
 * Why: Presage (the hidden sensor, sensing_client/presage_node) measures from the same camera. When the page
 * opened the camera while Presage was already running, macOS switched the camera's frame size under it and
 * Presage's engine aborted (OpenCV "prevPyr size == nextPyr size", 2026-10-11). So:
 *   1. the app opens the camera when it loads (App.tsx), before anyone starts an interview;
 *   2. it tells the backend (POST /api/sensor/page-camera), and the sensor waits for that before it starts
 *      Presage, so Presage always opens the camera after the page and nothing changes under it;
 *   3. the stream is kept for the life of the window. Moving between pages reuses it, and hiding the preview
 *      only hides the video: stopping and re-opening the camera could change its mode again.
 *
 * The preview prefers the Mac's own camera, like the sensor: macOS can make an iPhone (Continuity Camera) the
 * default, and then the page would show a different camera from the one Presage measures.
 */
import { apiUrl, IS_MOCK } from './config.ts'

const BUILT_IN_CAMERA = /facetime|built-in|macbook/i
const NOT_A_WEBCAM = /iphone|ipad|desk view/i
const VIDEO: MediaTrackConstraints = { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } }

/** VITE_CAMERA_PREVIEW=0 (run_demo.py --no-preview): the page never opens the camera; Presage has it alone. */
export const PREVIEW_ENABLED = import.meta.env.VITE_CAMERA_PREVIEW !== '0'

export const hasCamera = () => typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia

/** The default camera, unless it's an iPhone and the Mac has its own. Labels are readable only after access. */
async function preferBuiltIn(stream: MediaStream): Promise<MediaStream> {
  const label = stream.getVideoTracks()[0]?.label ?? ''
  if (!NOT_A_WEBCAM.test(label)) return stream
  const devices = await navigator.mediaDevices.enumerateDevices()
  const builtIn = devices.find((d) => d.kind === 'videoinput' && BUILT_IN_CAMERA.test(d.label))
  if (!builtIn) return stream
  try {
    const swapped = await navigator.mediaDevices.getUserMedia({ video: { ...VIDEO, deviceId: { exact: builtIn.deviceId } }, audio: false })
    stream.getTracks().forEach((t) => t.stop())
    return swapped
  } catch {
    return stream // keep what we have rather than lose the preview
  }
}

/** Tell the backend the page holds the camera, so the sensor can start Presage now. Best effort. */
function reportOpen(): void {
  if (IS_MOCK) return
  fetch(apiUrl('/api/sensor/page-camera'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ open: true }),
  }).catch(() => {
    // The sensor starts Presage on its own after a while anyway.
  })
}

let pending: Promise<MediaStream> | null = null

/** The app's one camera stream. Opened on first call; a failed attempt (denied, no camera) can be retried. */
export function sharedCamera(): Promise<MediaStream> {
  if (!pending) {
    pending = navigator.mediaDevices
      .getUserMedia({ video: { ...VIDEO, facingMode: 'user' }, audio: false })
      .then(preferBuiltIn)
      .then((stream) => {
        reportOpen()
        return stream
      })
      .catch((err: unknown) => {
        pending = null
        throw err
      })
  }
  return pending
}

/** Open the camera as the app loads, so Presage can start after it (see the top of this file). */
export function openCameraEarly(): void {
  if (PREVIEW_ENABLED && hasCamera()) sharedCamera().catch(() => {})
}
