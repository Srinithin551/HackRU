/**
 * MockSessionEngine: Brain's run_session (brain.md), the sensing stream and
 * Data's stress/baseline in one deterministic object. Everything is driven by a
 * Scheduler, so the same seed + the same client events give the same stream.
 *
 * DOM-free.
 */
import type {
  Baseline,
  ClientEvent,
  CoachFlagEvent,
  InterviewType,
  QuestionKind,
  ServerEvent,
  SessionState,
} from '../types.ts'
import { estimateSpeechMs, mockAudioUrl } from './audio.ts'
import { CLOSING_LINE, createFillerPicker, getScenario } from './scenario.ts'
import type { PlannedQuestion, Scenario } from './scenario.ts'
import { realScheduler } from './scheduler.ts'
import type { Scheduler, TimerId } from './scheduler.ts'
import {
  computeBaseline,
  createIdGenerator,
  flagFor,
  isoUtc,
  maxOf,
  MIN_CONFIDENCE,
  round,
  roundOrNull,
  seededRng,
  stressScore,
  VitalsModel,
  watcherLevel,
  windowStats,
} from './sim.ts'
import type { SimReading } from './sim.ts'

export type EnginePhase = 'idle' | 'running' | 'done' | 'ended'

export interface EngineTiming {
  vitalsIntervalMs: number
  watcherIntervalMs: number
  /** Watcher window (Brain reads get_window_stats(session, 10)). */
  watcherWindowS: number
  flagCooldownMs: number
  /** wait_answer timeout: a dropped tab doesn't hang the loop. */
  answerTimeoutMs: number
  /** Filler → next question (Gemini decide_next latency). */
  thinkingMs: number
  /** final_report latency: wrap-up → done + session_ended. */
  reportMs: number
  /** Brain's CLOSING_PAUSE_S: lets the closing line finish before the report is announced. */
  closingPauseMs: number
  /** Brain's CALIBRATION_MIN_S: a short first answer gets a second small-talk prompt. */
  calibrationMinMs: number
}

export const DEFAULT_TIMING: EngineTiming = {
  vitalsIntervalMs: 1_000,
  watcherIntervalMs: 3_000,
  watcherWindowS: 10,
  flagCooldownMs: 12_000,
  answerTimeoutMs: 120_000,
  thinkingMs: 1_500,
  reportMs: 2_500,
  closingPauseMs: 4_000,
  calibrationMinMs: 35_000,
}

/** backend/brain/lines.py CALIBRATION_EXTRA_LINES (the non-presentation variant). */
const CALIBRATION_EXTRA_LINE = 'What got you interested in this kind of work?'

/** Brain's thresholds (PLAN.md §3). */
export const HIGH_STRESS = 70

export interface EngineConfig {
  sessionId: string
  userName: string
  interviewType: InterviewType
  roleTitle: string
  /** Defaults to the session id. */
  seed?: number | string
  scheduler?: Scheduler
  /** Play the client too: answer_started when the audio would end, answer_done with a canned transcript. */
  autoAnswer?: boolean
  timing?: Partial<EngineTiming>
}

/** One stored vitals row, time in epoch ms. */
export type RecordedReading = SimReading

export interface RecordedQuestion {
  id: string
  idx: number
  kind: QuestionKind
  text: string
  transcript: string | null
  created_at: number
  answer_started_at: number | null
  answered_at: number | null
}

export interface RecordedFlag extends CoachFlagEvent {
  /** Question being answered when the flag fired. */
  question_id: string | null
  /** Window stress that triggered it. */
  stress: number
}

/** Everything Data would have stored, for buildReplay() / buildReport(). Times are epoch ms. */
export interface SessionRecording {
  session: {
    id: string
    user_name: string
    interview_type: InterviewType
    role_title: string
    started_at: number | null
    ended_at: number | null
    baseline: Baseline | null
    baselineFallback: boolean
  }
  readings: RecordedReading[]
  questions: RecordedQuestion[]
  flags: RecordedFlag[]
}

interface ActiveQuestion {
  record: RecordedQuestion
  planned: PlannedQuestion | null
  plannedIndex: number | null
  /** Whether this question's body reaction has been placed on the timeline. */
  anchored: boolean
  done: boolean
  timeoutId: TimerId | null
}

type Listener = (event: ServerEvent) => void

export class MockSessionEngine {
  readonly config: EngineConfig
  private readonly scheduler: Scheduler
  private readonly timing: EngineTiming
  private readonly scenario: Scenario
  private readonly nextId: () => string
  private readonly pickFiller: () => string
  private readonly model: VitalsModel
  private readonly listeners = new Set<Listener>()
  private readonly recording: SessionRecording
  private readonly flowTimers = new Set<TimerId>()
  private readonly followUpAsked = new Set<number>()
  private state: EnginePhase = 'idle'
  private stopped = false
  private current: ActiveQuestion | null = null
  private plannedIndex = -1
  private idx = 0
  private extraCalibrationAsked = false
  private listening = false
  private baseline: Baseline | null = null
  private lastFlagAt = Number.NEGATIVE_INFINITY
  /** Flag phrasings already shown, so no interrupt ever repeats itself (see GodInterrupt). */
  private readonly usedFlagTexts = new Set<string>()
  private vitalsTimer: TimerId | null = null
  private watcherTimer: TimerId | null = null
  private lastQuestion: ServerEvent | null = null
  private lastState: ServerEvent | null = null
  private lastEnded: ServerEvent | null = null

  constructor(config: EngineConfig) {
    this.config = config
    this.scheduler = config.scheduler ?? realScheduler
    this.timing = { ...DEFAULT_TIMING, ...config.timing }
    this.scenario = getScenario(config.interviewType)
    const seed = config.seed ?? config.sessionId
    this.nextId = createIdGenerator(seededRng(seed, 'ids'))
    this.pickFiller = createFillerPicker(seededRng(seed, 'fillers'))
    this.model = new VitalsModel(seededRng(seed, 'vitals'))
    this.recording = {
      session: {
        id: config.sessionId,
        user_name: config.userName,
        interview_type: config.interviewType,
        role_title: config.roleTitle,
        started_at: null,
        ended_at: null,
        baseline: null,
        baselineFallback: false,
      },
      readings: [],
      questions: [],
      flags: [],
    }
  }

  get phase(): EnginePhase {
    return this.state
  }

  get sessionId(): string {
    return this.config.sessionId
  }

  /** Number of planned main questions (QuestionEvent.total). */
  get totalQuestions(): number {
    return this.scenario.questions.length
  }

  /** Starts calibration + the interview loop. A second call is a no-op. */
  start(): void {
    if (this.state !== 'idle' || this.stopped) return
    this.state = 'running'
    this.recording.session.started_at = this.now()
    this.emitState('calibrating')
    this.vitalsTimer = this.scheduler.setInterval(() => this.tickVitals(), this.timing.vitalsIntervalMs)
    this.ask(this.scenario.calibration.text, 'calibration', null, null)
  }

  /** Every server event from now on. Returns an unsubscribe function. */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /**
   * What a client connecting late needs to catch up (the current question and
   * state, or session_ended). The real server doesn't replay; the mock does so a
   * page reload mid-session keeps working.
   */
  latestEvents(): ServerEvent[] {
    if (this.lastEnded) return [this.lastEnded]
    return [this.lastQuestion, this.lastState].filter((e): e is ServerEvent => e !== null)
  }

  /** A client → server message (what ws.inbox delivers to Brain). */
  receive(event: ClientEvent): void {
    if (this.stopped || this.state !== 'running') return
    const active = this.current
    switch (event.type) {
      case 'answer_started': {
        if (!active || active.done || active.record.id !== event.data.question_id) return
        if (active.record.answer_started_at !== null) return
        active.record.answer_started_at = this.now()
        if (!active.anchored) this.anchor(active, this.now())
        return
      }
      case 'answer_done': {
        if (!active || active.done || active.record.id !== event.data.question_id) return
        this.finishAnswer(active, event.data.transcript ?? '')
        return
      }
      case 'end_session':
        this.endEarly()
        return
    }
  }

  /** Tears down every timer. The engine emits nothing afterwards. */
  stop(): void {
    this.stopped = true
    this.clearAllTimers()
  }

  /** Live recording (mutated while the session runs). */
  getRecording(): SessionRecording {
    return this.recording
  }

  // -------------------------------------------------------------------------
  // Interview loop
  // -------------------------------------------------------------------------

  private ask(text: string, kind: QuestionKind, planned: PlannedQuestion | null, plannedIndex: number | null): void {
    const now = this.now()
    const record: RecordedQuestion = {
      id: this.nextId(),
      idx: kind === 'calibration' ? 0 : ++this.idx,
      kind,
      text,
      transcript: null,
      created_at: now,
      answer_started_at: null,
      answered_at: null,
    }
    this.recording.questions.push(record)
    const active: ActiveQuestion = { record, planned, plannedIndex, anchored: false, done: false, timeoutId: null }
    this.current = active

    // Mirrors interviewer.ask(): calibration stays in 'calibrating' (no asking/listening, so the
    // overlay doesn't flicker); every other question goes asking → question → listening.
    const calibrating = kind === 'calibration'
    if (!calibrating) this.emitState('asking')
    this.emit({
      type: 'question',
      data: {
        question_id: record.id,
        // Brain's planned_number: 0 during calibration, then 1..total. A follow-up keeps its
        // parent's number (the sequential idx, which counts follow-ups, is only in the replay).
        index: plannedIndex === null ? 0 : plannedIndex + 1,
        total: this.totalQuestions,
        kind,
        text,
        audio_url: mockAudioUrl(text),
      },
    })
    // The client opens the mic itself when the audio ends.
    this.listening = true
    if (!calibrating) this.emitState('listening')

    active.timeoutId = this.later(() => this.finishAnswer(active, ''), this.timing.answerTimeoutMs)

    const audioMs = estimateSpeechMs(text)
    const overlay = this.overlayFor(active)
    if (overlay) {
      // Without answer_started, anchor the reaction to "question time + audio length".
      this.later(() => {
        if (!active.anchored && !active.done) this.anchor(active, now + audioMs)
      }, audioMs + overlay.offsetMs)
    }

    if (this.config.autoAnswer) {
      const script = this.autoScript(active)
      const questionId = record.id
      this.later(() => this.receive({ type: 'answer_started', data: { question_id: questionId } }), audioMs)
      this.later(
        () => this.receive({ type: 'answer_done', data: { question_id: questionId, transcript: script.transcript } }),
        audioMs + script.durationMs,
      )
    }
  }

  private finishAnswer(active: ActiveQuestion, transcript: string): void {
    if (active.done || this.state !== 'running') return
    active.done = true
    if (active.timeoutId !== null) this.clearTimer(active.timeoutId)
    active.record.transcript = transcript
    active.record.answered_at = this.now()
    this.listening = false

    if (active.record.kind === 'calibration') {
      // interviewer.calibrate(): keep chatting until CALIBRATION_MIN_S so Data gets enough samples.
      const elapsed = this.now() - (this.recording.session.started_at ?? this.now())
      if (elapsed < this.timing.calibrationMinMs && !this.extraCalibrationAsked) {
        this.extraCalibrationAsked = true
        this.ask(CALIBRATION_EXTRA_LINE, 'calibration', null, null)
        return
      }
      this.finishCalibration()
      this.askNextPlanned()
      return
    }

    this.emit({ type: 'filler', data: { audio_url: mockAudioUrl(this.pickFiller()) } })
    this.emitState('thinking')
    this.later(() => this.decideNext(active), this.timing.thinkingMs)
  }

  /** data.compute_baseline + stress backfill, then the watcher starts. */
  private finishCalibration(): void {
    const startedAt = this.recording.session.started_at ?? this.now()
    const { baseline, fallback } = computeBaseline(this.recording.readings, startedAt, this.now())
    this.baseline = baseline
    this.recording.session.baseline = baseline
    this.recording.session.baselineFallback = fallback
    for (const reading of this.recording.readings) reading.stress = roundOrNull(stressScore(reading, baseline), 1)
    this.watcherTimer = this.scheduler.setInterval(() => this.tickWatcher(), this.timing.watcherIntervalMs)
  }

  /** Stand-in for gemini.decide_next: ease off after a spike, else the next planned question. */
  private decideNext(answered: ActiveQuestion): void {
    if (this.state !== 'running') return
    const { planned, plannedIndex } = answered
    if (answered.record.kind === 'main' && planned?.followUp && plannedIndex !== null && !this.followUpAsked.has(plannedIndex)) {
      const peak = this.answerPeak(answered.record)
      if (peak !== null && peak > HIGH_STRESS) {
        this.followUpAsked.add(plannedIndex)
        this.ask(planned.followUp, 'follow_up', planned, plannedIndex)
        return
      }
    }
    this.askNextPlanned()
  }

  private askNextPlanned(): void {
    const next = this.plannedIndex + 1
    const planned = this.scenario.questions[next]
    if (!planned) {
      this.wrapUp()
      return
    }
    this.plannedIndex = next
    this.ask(planned.text, 'main', planned, next)
  }

  /** Brain's wrap-up: 'thinking', closing line as a filler, a pause, then the report. */
  private wrapUp(): void {
    this.stopWatcher()
    this.current = null
    this.emitState('thinking')
    this.emit({ type: 'filler', data: { audio_url: mockAudioUrl(CLOSING_LINE) } })
    this.state = 'done'
    this.later(() => this.finish(), this.timing.closingPauseMs)
  }

  /** end_session: no closing line, straight to the report. */
  private endEarly(): void {
    if (this.state !== 'running') return
    this.clearFlowTimers()
    this.stopWatcher()
    this.current = null
    this.emitState('thinking')
    this.state = 'done'
    this.finish()
  }

  /**
   * Report generation, then interviewer.finish(): 'done' and session_ended back to back.
   * The report already exists when session_ended arrives (getReport stops returning null).
   */
  private finish(): void {
    this.listening = false
    this.state = 'done'
    this.later(() => {
      this.recording.session.ended_at = this.now()
      this.state = 'ended'
      this.clearAllTimers()
      this.emitState('done')
      const id = encodeURIComponent(this.config.sessionId)
      const ended: ServerEvent = {
        type: 'session_ended',
        data: { report_url: `/api/sessions/${id}/report`, replay_url: `/api/sessions/${id}/replay` },
      }
      this.lastEnded = ended
      this.emit(ended)
    }, this.timing.reportMs)
  }

  // -------------------------------------------------------------------------
  // Sensing + watcher
  // -------------------------------------------------------------------------

  private tickVitals(): void {
    const now = this.now()
    const sample = this.model.sample(now)
    const reading: RecordedReading = {
      time: now,
      heart_rate: roundOrNull(sample.heart_rate, 1),
      breathing_rate: roundOrNull(sample.breathing_rate, 1),
      focus: roundOrNull(sample.focus, 2),
      expression: sample.expression,
      confidence: round(sample.confidence, 2),
      stress: null,
    }
    reading.stress = roundOrNull(stressScore(reading, this.baseline), 1)
    this.recording.readings.push(reading)
    this.emit({
      type: 'vitals',
      data: {
        ts: isoUtc(now),
        heart_rate: reading.heart_rate,
        breathing_rate: reading.breathing_rate,
        focus: reading.focus,
        stress: reading.stress,
      },
    })
  }

  /** brain.md watch_stress: every 3 s while listening, 12 s cooldown between flags. */
  private tickWatcher(): void {
    if (!this.listening || this.state !== 'running') return
    const now = this.now()
    const w = windowStats(this.recording.readings, now, this.timing.watcherWindowS, this.baseline)
    if (w.stress === null) return
    const level = watcherLevel(w.stress)
    if (!level || now - this.lastFlagAt <= this.timing.flagCooldownMs) return
    const { message, command } = flagFor(w, this.baseline, level, this.usedFlagTexts)
    const flag: CoachFlagEvent = { ts: isoUtc(now), level, message, command }
    this.lastFlagAt = now
    this.recording.flags.push({ ...flag, question_id: this.current?.record.id ?? null, stress: round(w.stress, 1) })
    this.emit({ type: 'coach_flag', data: flag })
  }

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  private overlayFor(active: ActiveQuestion): { offsetMs: number } | null {
    const planned = active.planned
    if (active.record.kind !== 'main' || !planned?.overlay) return null
    return { offsetMs: planned.overlayOffsetMs }
  }

  private anchor(active: ActiveQuestion, answerStartMs: number): void {
    active.anchored = true
    const planned = active.planned
    if (this.overlayFor(active) && planned?.overlay) {
      this.model.addOverlay(planned.overlay, answerStartMs + planned.overlayOffsetMs)
    }
  }

  private autoScript(active: ActiveQuestion): { transcript: string; durationMs: number } {
    const { kind } = active.record
    if (kind === 'calibration') {
      return { transcript: this.scenario.calibration.answer, durationMs: this.scenario.calibration.answerMs }
    }
    const planned = active.planned
    if (kind === 'follow_up' && planned) {
      return { transcript: planned.followUpAnswer ?? '', durationMs: planned.followUpAnswerMs }
    }
    return { transcript: planned?.answer ?? '', durationMs: planned?.answerMs ?? 20_000 }
  }

  /** Max stress over the answer window (COALESCE(answer_started_at, created_at) .. answered_at). */
  private answerPeak(record: RecordedQuestion): number | null {
    const from = record.answer_started_at ?? record.created_at
    const to = record.answered_at ?? this.now()
    return maxOf(
      this.recording.readings
        .filter((r) => r.time >= from && r.time <= to && r.confidence >= MIN_CONFIDENCE)
        .map((r) => r.stress),
    )
  }

  /** Like interviewer.set_state(): repeating the current state is a no-op. */
  private emitState(state: SessionState): void {
    if (this.lastState?.type === 'state' && this.lastState.data.state === state) return
    const event: ServerEvent = { type: 'state', data: { state } }
    this.lastState = event
    this.emit(event)
  }

  private emit(event: ServerEvent): void {
    if (this.stopped) return
    if (event.type === 'question') this.lastQuestion = event
    for (const listener of [...this.listeners]) {
      try {
        listener(event)
      } catch (err) {
        console.error('[mock engine] listener failed on', event.type, err)
      }
    }
  }

  private now(): number {
    return this.scheduler.now()
  }

  /** One-shot flow timer; cleared by end_session / stop(). */
  private later(fn: () => void, ms: number): TimerId {
    const id: TimerId = this.scheduler.setTimeout(() => {
      this.flowTimers.delete(id)
      if (!this.stopped) fn()
    }, ms)
    this.flowTimers.add(id)
    return id
  }

  private clearTimer(id: TimerId): void {
    this.scheduler.clear(id)
    this.flowTimers.delete(id)
  }

  private clearFlowTimers(): void {
    for (const id of this.flowTimers) this.scheduler.clear(id)
    this.flowTimers.clear()
  }

  private stopWatcher(): void {
    if (this.watcherTimer !== null) this.scheduler.clear(this.watcherTimer)
    this.watcherTimer = null
  }

  private clearAllTimers(): void {
    this.clearFlowTimers()
    this.stopWatcher()
    if (this.vitalsTimer !== null) this.scheduler.clear(this.vitalsTimer)
    this.vitalsTimer = null
  }
}
