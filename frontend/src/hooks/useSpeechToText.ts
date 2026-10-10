/**
 * Speech-to-text behind one hook (frontend.md: "Keep the STT code behind one
 * useSpeechToText() hook so swapping is easy", e.g. to ElevenLabs STT later).
 *
 * Uses the browser Web Speech API (Chrome; Safari via webkitSpeechRecognition):
 *  - continuous + interimResults, so words appear live while you talk;
 *  - Chrome stops the recognizer on its own every so often, so onend restarts it
 *    while we still want to listen, keeping text from earlier runs;
 *  - auto-stop: SILENCE_MS of silence after at least MIN_SPEECH_MS of speech calls
 *    onAutoStop(transcript), same as pressing "I'm done".
 *
 * If the browser has no recognizer, `supported` is false and the page offers a
 * text box instead.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { MIN_SPEECH_MS, SILENCE_MS } from '../lib/constants.ts'

// The Web Speech API isn't in TypeScript's DOM lib, so declare the parts we use.
interface RecognitionAlternative {
  transcript: string
}
interface RecognitionResult {
  readonly isFinal: boolean
  readonly length: number
  [index: number]: RecognitionAlternative
}
interface RecognitionEvent {
  readonly results: { readonly length: number; [index: number]: RecognitionResult }
}
interface Recognition {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: RecognitionEvent) => void) | null
  onend: (() => void) | null
  onerror: ((event: { error: string }) => void) | null
  start(): void
  stop(): void
  abort(): void
}
type RecognitionCtor = new () => Recognition

function getRecognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

const join = (...parts: string[]) => parts.map((p) => p.trim()).filter(Boolean).join(' ')

interface Options {
  /** Called after auto-stop with the final transcript. */
  onAutoStop?: (transcript: string) => void
  lang?: string
}

export function useSpeechToText({ onAutoStop, lang = 'en-US' }: Options = {}) {
  const [supported] = useState(() => getRecognitionCtor() !== null)
  const [listening, setListening] = useState(false)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)

  const recognitionRef = useRef<Recognition | null>(null)
  const wantRef = useRef(false) // we want to be listening (restart on Chrome's spontaneous onend)
  const committedRef = useRef('') // text from earlier recognizer runs in this answer
  const runRef = useRef('') // text from the current run (final + interim)
  const firstSpeechAtRef = useRef<number | null>(null)
  const lastSpeechAtRef = useRef<number | null>(null)
  const silenceTimerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const onAutoStopRef = useRef(onAutoStop)
  useEffect(() => {
    onAutoStopRef.current = onAutoStop
  })

  const current = () => join(committedRef.current, runRef.current)

  /** Stops listening and returns everything heard for this answer. */
  const stop = useCallback((): string => {
    wantRef.current = false
    clearInterval(silenceTimerRef.current)
    try {
      recognitionRef.current?.stop()
    } catch {
      // not running
    }
    setListening(false)
    return current()
  }, [])

  /** Stops and clears the transcript (a new question arrived). */
  const reset = useCallback(() => {
    stop()
    committedRef.current = ''
    runRef.current = ''
    firstSpeechAtRef.current = null
    lastSpeechAtRef.current = null
    setText('')
  }, [stop])

  const start = useCallback(() => {
    const Ctor = getRecognitionCtor()
    if (!Ctor) return
    committedRef.current = ''
    runRef.current = ''
    firstSpeechAtRef.current = null
    lastSpeechAtRef.current = null
    setText('')
    setError(null)

    if (!recognitionRef.current) {
      const recognition = new Ctor()
      recognition.continuous = true
      recognition.interimResults = true
      recognition.lang = lang
      recognition.onresult = (event) => {
        // Results accumulate per run; rebuild this run's text from all of them.
        let run = ''
        for (let i = 0; i < event.results.length; i++) run += ` ${event.results[i][0]?.transcript ?? ''}`
        runRef.current = run.trim()
        const now = Date.now()
        if (runRef.current) {
          firstSpeechAtRef.current ??= now
          lastSpeechAtRef.current = now
        }
        setText(current())
      }
      recognition.onend = () => {
        committedRef.current = current()
        runRef.current = ''
        if (!wantRef.current) return
        setTimeout(() => {
          if (!wantRef.current) return
          try {
            recognition.start()
          } catch {
            // already started
          }
        }, 150)
      }
      recognition.onerror = (event) => {
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
          wantRef.current = false
          clearInterval(silenceTimerRef.current)
          setListening(false)
          setError('Microphone access was blocked. Allow it in the address bar, or type your answer.')
        }
        // 'no-speech' / 'aborted' / 'network': onend follows and restarts if we still want to listen.
      }
      recognitionRef.current = recognition
    }

    wantRef.current = true
    try {
      recognitionRef.current.start()
    } catch {
      // already started
    }
    setListening(true)

    clearInterval(silenceTimerRef.current)
    silenceTimerRef.current = setInterval(() => {
      const first = firstSpeechAtRef.current
      const last = lastSpeechAtRef.current
      if (!wantRef.current || first === null || last === null) return
      if (last - first >= MIN_SPEECH_MS && Date.now() - last >= SILENCE_MS) {
        const transcript = stop()
        onAutoStopRef.current?.(transcript)
      }
    }, 250)
  }, [lang, stop])

  // Release the mic on unmount.
  useEffect(
    () => () => {
      wantRef.current = false
      clearInterval(silenceTimerRef.current)
      recognitionRef.current?.abort()
    },
    [],
  )

  return { supported, listening, text, error, start, stop, reset }
}
