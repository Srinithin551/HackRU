/**
 * Mock interviewer audio. There are no mp3s in mock mode, so an audio URL carries
 * its own text ("mock-tts:Tell%20me%20about...") and the live page speaks it with
 * speechSynthesis or just waits estimateSpeechMs(text).
 *
 * DOM-free.
 */

export const MOCK_AUDIO_SCHEME = 'mock-tts:'

/** Builds a mock audio URL that encodes `text`. */
export function mockAudioUrl(text: string): string {
  return `${MOCK_AUDIO_SCHEME}${encodeURIComponent(text)}`
}

/** True for URLs made by mockAudioUrl(). */
export function isMockAudioUrl(url: string): boolean {
  return url.startsWith(MOCK_AUDIO_SCHEME)
}

/** The text inside a mock audio URL, or '' for any other URL. */
export function mockAudioText(url: string): string {
  if (!isMockAudioUrl(url)) return ''
  const encoded = url.slice(MOCK_AUDIO_SCHEME.length)
  try {
    return decodeURIComponent(encoded)
  } catch {
    return encoded
  }
}

const WORDS_PER_SECOND = 2.6
const LEAD_IN_MS = 400

/** Rough spoken duration of `text` (~2.6 words/s plus a short lead-in), in ms. */
export function estimateSpeechMs(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length
  return Math.round(LEAD_IN_MS + (words / WORDS_PER_SECOND) * 1000)
}
