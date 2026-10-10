import { estimateSpeechMs, isMockAudioUrl, mockAudioText } from '../mocks/audio.ts'
import { resolveAudioUrl } from './config.ts'

/** Stops whatever clip is playing now. One voice channel for the whole page. */
let stopCurrent: (() => void) | null = null

/**
 * Plays one interviewer clip and calls `onEnded` exactly once when it finishes
 * (or immediately if there is nothing to play). Returns a function that stops it.
 *
 * One voice at a time: starting a clip always silences the previous one, whoever started it
 * (question audio, filler, a replayed question after a reconnect, a second socket in StrictMode).
 * The interviewer can never talk over itself.
 *
 * Three cases, matching what the backend can send:
 *  - null audio_url (TTS unavailable): Brain says "show the text and send answer_started
 *    right away", so we end immediately.
 *  - "mock-tts:<text>" (VITE_MOCK=1): spoken with the browser's speechSynthesis, with a
 *    timer fallback so a missing voice can't stall the session.
 *  - a real mp3 URL: played with an <audio> element. Load/play errors also count as
 *    "ended" so the mic still opens; a broken clip must not freeze the interview.
 */
export function playClip(url: string | null, onEnded: () => void = () => {}): () => void {
  stopCurrent?.()
  stopCurrent = null

  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    if (stopCurrent === stop) stopCurrent = null
    onEnded()
  }
  let stop: () => void = () => {}

  const src = resolveAudioUrl(url)
  if (!src) {
    queueMicrotask(finish)
    return stop
  }

  if (isMockAudioUrl(src)) {
    const text = mockAudioText(src)
    // Generous fallback: only fires if speechSynthesis never reports `end`.
    const timer = setTimeout(finish, estimateSpeechMs(text) * 1.5 + 2_000)
    const synth = typeof window !== 'undefined' ? window.speechSynthesis : undefined
    if (synth) {
      const utterance = new SpeechSynthesisUtterance(text)
      utterance.onend = finish
      utterance.onerror = finish
      synth.speak(utterance)
    }
    stop = () => {
      finished = true
      clearTimeout(timer)
      synth?.cancel()
    }
    stopCurrent = stop
    return stop
  }

  const audio = new Audio(src)
  audio.onended = finish
  audio.onerror = finish
  audio.play().catch(finish) // autoplay blocked or decode error: don't hang the answer flow
  stop = () => {
    finished = true
    audio.pause()
  }
  stopCurrent = stop
  return stop
}
