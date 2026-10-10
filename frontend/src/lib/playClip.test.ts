import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { playClip } from './playClip.ts'

/** Minimal stand-in for HTMLAudioElement: records play/pause so overlap is observable. */
class FakeAudio {
  static all: FakeAudio[] = []
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  playing = false
  readonly src: string

  constructor(src: string) {
    this.src = src
    FakeAudio.all.push(this)
  }

  play() {
    this.playing = true
    return Promise.resolve()
  }

  pause() {
    this.playing = false
  }

  end() {
    this.playing = false
    this.onended?.()
  }
}

const playingNow = () => FakeAudio.all.filter((a) => a.playing)

describe('playClip: one voice at a time', () => {
  beforeEach(() => {
    FakeAudio.all = []
    vi.stubGlobal('Audio', FakeAudio)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('silences the previous clip when a new one starts, whoever started it', () => {
    const firstEnded = vi.fn()
    playClip('/audio/question.mp3', firstEnded)
    playClip('/audio/closing.mp3') // e.g. a filler or a replayed question from another listener
    expect(playingNow().map((a) => a.src)).toEqual(['/audio/closing.mp3'])
    expect(firstEnded).not.toHaveBeenCalled() // interrupted, not finished: no answer_started for it
  })

  it('never has two clips playing across many rapid starts', () => {
    for (let i = 0; i < 10; i++) {
      playClip(`/audio/${i}.mp3`)
      expect(playingNow()).toHaveLength(1)
    }
  })

  it('calls onEnded once when the clip finishes, and a finished clip is not stopped later', () => {
    const ended = vi.fn()
    playClip('/audio/a.mp3', ended)
    const [a] = FakeAudio.all
    a.end()
    a.onended?.()
    expect(ended).toHaveBeenCalledTimes(1)
    playClip('/audio/b.mp3')
    expect(playingNow().map((x) => x.src)).toEqual(['/audio/b.mp3'])
  })

  it('a null url (no TTS) ends immediately and stops whatever was playing', async () => {
    playClip('/audio/a.mp3')
    const ended = vi.fn()
    playClip(null, ended)
    expect(playingNow()).toHaveLength(0)
    await Promise.resolve()
    expect(ended).toHaveBeenCalledTimes(1)
  })
})
