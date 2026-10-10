import { estimateSpeechMs, isMockAudioUrl, mockAudioText } from '../mocks/audio.ts'
import { resolveAudioUrl } from './config.ts'

/**
 * Plays one interviewer clip and calls `onEnded` exactly once when it finishes
 * (or immediately if there is nothing to play). Returns a function that stops it.
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
  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    onEnded()
  }

  const src = resolveAudioUrl(url)
  if (!src) {
    queueMicrotask(finish)
    return () => {}
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
    return () => {
      finished = true
      clearTimeout(timer)
      synth?.cancel()
    }
  }

  const audio = new Audio(src)
  audio.onended = finish
  audio.onerror = finish
  audio.play().catch(finish) // autoplay blocked or decode error: don't hang the answer flow
  return () => {
    finished = true
    audio.pause()
  }
}
