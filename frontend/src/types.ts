/**
 * Shared contracts. Mirrors docs/PLAN.md §3 (backend/contracts.py), the replay
 * shape in docs/roles/data.md and the report shape in docs/roles/brain.md.
 *
 * Do not reshape anything here without telling the team: these are the wire formats.
 * Pydantic `Optional[...]` fields serialize as `null`, so they are `T | null` here.
 */

/** ISO-8601 UTC timestamp, e.g. "2026-10-10T22:01:05Z". */
export type IsoDateTime = string

// ---------------------------------------------------------------------------
// PLAN.md §3 models
// ---------------------------------------------------------------------------

export type Expression = 'neutral' | 'happy' | 'surprised' | 'frown' | 'confused' | 'unknown'

/** One reading per second from the sensing client (POST /api/vitals). */
export interface VitalsReading {
  /** Omit to let the backend use the active session. */
  session_id?: string | null
  ts: IsoDateTime
  /** bpm */
  heart_rate?: number | null
  /** breaths per minute */
  breathing_rate?: number | null
  expression?: Expression | null
  /** 0 to 1, face/signal quality. Data ignores readings under MIN_CONFIDENCE (0.4 by default). */
  confidence: number
}

export interface VitalsBatch {
  readings: VitalsReading[]
}

export interface Baseline {
  heart_rate: number
  breathing_rate: number
  n_samples: number
}

/** "How is the person right now" (Data → Brain). */
export interface WindowStats {
  seconds: number
  n_samples: number
  hr_mean: number | null
  /** % above/below baseline */
  hr_delta_pct: number | null
  br_mean: number | null
  br_delta_pct: number | null
  dominant_expression: string | null
  /** 0 to 100 */
  stress: number | null
}

/** "How did they react to this question". */
export interface QuestionStats {
  question_id: string
  duration_s: number
  n_samples: number
  hr_mean: number | null
  hr_peak: number | null
  hr_delta_pct: number | null
  br_mean: number | null
  stress_mean: number | null
  stress_peak: number | null
  /** Seconds into the answer when stress peaked. */
  peak_offset_s: number | null
}

// ---------------------------------------------------------------------------
// REST: POST /api/sessions, GET /api/sessions/active
// ---------------------------------------------------------------------------

/**
 * Interview types offered on the setup page. Brain's specs.normalize_type() maps
 * these onto its specs: software_engineering → swe, behavioral → behavioral,
 * class_presentation → presentation, demo → demo (3 questions, 240 s).
 */
export const INTERVIEW_TYPES = [
  { value: 'software_engineering', label: 'Software Engineering' },
  { value: 'behavioral', label: 'Behavioral' },
  { value: 'class_presentation', label: 'Class Presentation' },
  { value: 'demo', label: 'Quick Demo' },
] as const

export type InterviewType = (typeof INTERVIEW_TYPES)[number]['value']

export interface CreateSessionRequest {
  user_name: string
  interview_type: InterviewType
  role_title: string
}

export interface CreateSessionResponse {
  session_id: string
}

/**
 * POST /api/sessions/{id}/material: a file imported on the setup page (slides, notes, a résumé…) that Judge
 * draws its questions from. Sent before /start. `kind` is "pdf" (handed to Gemini as-is) or "text".
 */
export interface MaterialUploadResponse {
  name: string
  kind: 'pdf' | 'text'
  /** Characters of text read from the file (0 for a PDF). */
  chars: number
}

/** File types the backend can read (backend/brain/material.py). */
export const MATERIAL_ACCEPT = '.pdf,.pptx,.docx,.txt,.md,.markdown,.csv'
export const MATERIAL_MAX_BYTES = 10 * 1024 * 1024

/** POST /api/sessions/{id}/start. Brain made it idempotent: repeat calls report the current status. */
export interface StartSessionResponse {
  session_id: string
  status: 'started' | 'already_running' | 'finished'
}

/** `null` when no session is active. */
export type ActiveSessionResponse = { session_id: string } | null

/** GET /health (backend/main.py). Useful to show which pieces are real vs fake. */
export interface HealthResponse {
  ok: boolean
  /** BRAIN_FAKE=1: canned questions, rule-based decisions, no API keys needed. */
  fake: boolean
  gemini: boolean
  elevenlabs: boolean
  /** Mounted routers, e.g. ["brain", "data", "sensing"]. */
  routers: string[]
}

// ---------------------------------------------------------------------------
// REST: GET /api/sessions/{id}/replay (owner: Data, shape from data.md)
// ---------------------------------------------------------------------------

export type QuestionKind = 'calibration' | 'main' | 'follow_up'

export interface ReplaySession {
  id: string
  user_name: string | null
  started_at: IsoDateTime
  ended_at: IsoDateTime | null
  /** Null if the baseline was never computed. */
  baseline: { heart_rate: number; breathing_rate: number } | null
}

/** One 5-second bucket from the `vitals_5s` continuous aggregate. */
export interface ReplayPoint {
  t: IsoDateTime
  hr: number | null
  br: number | null
  stress: number | null
  stress_peak: number | null
}

export interface ReplayQuestion {
  id: string
  /** 0 = calibration; increments for every question asked, follow-ups included. */
  idx: number
  kind: QuestionKind
  text: string
  transcript: string | null
  answer_started_at: IsoDateTime | null
  answered_at: IsoDateTime | null
  stats: QuestionStats | null
}

export interface ReplayResponse {
  session: ReplaySession
  /** Ordered by bucket. */
  series: ReplayPoint[]
  questions: ReplayQuestion[]
  /**
   * PROPOSED EXTENSION, not in PLAN.md yet: coach flags fired during the session,
   * for the red/amber dots on the replay chart. Until Data/Brain add it, the live
   * page records flags client-side.
   */
  coach_flags?: CoachFlagEvent[]
}

// ---------------------------------------------------------------------------
// REST: GET /api/sessions/{id}/report (owner: Brain, shape from brain.md)
// ---------------------------------------------------------------------------

export interface QuestionFeedback {
  /** Matches ReplayQuestion.id. */
  question_id: string
  /** 1-2 sentences about the answer content. */
  what_happened: string
  /** Always cites numbers, e.g. "HR jumped 22% above baseline 8s in". */
  body_signal: string
  /** One concrete thing to do next time. */
  advice: string
}

export interface Report {
  /** 0 to 100, higher is calmer. */
  composure_score: number
  /** 2-3 sentences. */
  summary: string
  /** The single biggest spike, with numbers. */
  hardest_moment: string
  per_question: QuestionFeedback[]
  /** Exactly 3. */
  top_tips: string[]
  // Additive fields from Brain's build (brain.md "As built"). Optional so the mock and older
  // reports still type-check.
  /** The name typed on the setup page ("" if left blank), shown on the report and verdict pages. */
  candidate_name?: string
  /** False when no usable vitals were captured: composure_score is then a neutral 50, not a verdict. */
  composure_measured?: boolean
  /** For the red/amber dots on the replay chart. */
  coach_flags?: ReportCoachFlag[]
  /** The decision made after each answer (coach, redirect, ease_off, push, follow_up, next, wrap_up). */
  adaptations?: Adaptation[]
  generated_by?: 'gemini' | 'fallback' | 'fake'
  /** True when the candidate repeatedly looked away while answering (backend/brain/integrity.py). */
  suspected_cheating?: boolean
  /** Look-aways while answering, this session. */
  look_aways?: number
  integrity_flags?: ReportIntegrityFlag[]
}

export interface ReportIntegrityFlag extends IntegrityFlagEvent {
  question_id: string | null
}

export interface ReportCoachFlag {
  ts: IsoDateTime
  question_id: string | null
  level: CoachLevel
  stress: number
  message: string
  command?: string
}

export interface Adaptation {
  question_id: string
  /** The live coaching that was spoken, when action is "coach". */
  coaching?: string
  /**
   * coach = the answer was wrong, off-topic or inappropriate: the interviewer stepped out of the interview,
   * said what would happen in a real one and what to say instead, then moved on.
   * redirect = the answer didn't come through (garbled speech-to-text), so the interviewer asked again.
   */
  action: 'coach' | 'redirect' | 'follow_up' | 'ease_off' | 'push' | 'next' | 'wrap_up'
  answer_quality: number
  rationale: string
}

// ---------------------------------------------------------------------------
// REST: GET /api/sessions/{id}/insights (owner: Brain)
// ---------------------------------------------------------------------------

/** Which measured series a chart note explains. */
export type ChartVariable = 'heart_rate' | 'breathing' | 'composure'

/**
 * One Gemini-written note about what a variable did during one question, shown when you hover that
 * stretch of the replay chart. Generated on first request, so the report never waits on it.
 */
export interface ChartNote {
  /** Matches ReplayQuestion.id. */
  question_id: string
  variable: ChartVariable
  /** At most ~6 words, shown in bold. */
  headline: string
  /** 1-2 sentences citing a real number. */
  detail: string
}

export interface InsightsResponse {
  notes: ChartNote[]
}

// ---------------------------------------------------------------------------
// WebSocket /ws/{session_id}: every message is {"type": ..., "data": {...}}
// ---------------------------------------------------------------------------

export type SessionState = 'calibrating' | 'asking' | 'listening' | 'thinking' | 'done'

/**
 * Brain's state sequence (brain.md "As built"):
 *   calibrating (whole calibration, 1-2 small-talk prompts, no asking/listening)
 *   → per question: asking → listening → [answer] → filler event → thinking
 *   → end: thinking (closing line plays as a filler while the report is written) → done → session_ended
 */
export interface QuestionEvent {
  question_id: string
  /** Planned question number: 0 for calibration, then 1..total. A follow-up keeps its parent's number (check `kind`). */
  index: number
  /** Planned question count ("Q 2 of 5"). */
  total: number
  kind: QuestionKind
  /** The question, with at most a short lead-in. Never contains coaching. */
  text: string
  /**
   * Feedback on the previous answer, said outside the interview frame ("Let me step out of the interview
   * for a second…"): what would happen in a real interview and what a strong answer includes.
   * Spoken first, in the same clip as `text`. Null when there's no coaching.
   */
  coaching?: string | null
  /**
   * Absolute URL (PUBLIC_BASE_URL + "/audio/<hash>.mp3"), or null when TTS is unavailable:
   * then show the text and send answer_started right away. Play it via resolveAudioUrl().
   */
  audio_url: string | null
}

export interface FillerEvent {
  /** Filler or closing line. Doesn't change the question on screen. */
  audio_url: string
}

/** About one per second. Every metric is null while the face is lost. */
export interface VitalsEvent {
  ts: IsoDateTime
  heart_rate: number | null
  breathing_rate: number | null
  /** Null until the baseline exists, or when confidence is below the cutoff (0.4 by default). */
  stress: number | null
}

export type CoachLevel = 'rising' | 'high'

export interface CoachFlagEvent {
  ts: IsoDateTime
  level: CoachLevel
  /** What Judge observed, citing the number that triggered it. On screen only, never spoken. */
  message: string
  /**
   * The single thing to do, shown as "Please <command>." The box removes itself once it has had time to
   * be read. Optional so older sessions and fixtures still type-check.
   */
  command?: string
}

/**
 * "Suspected of cheating" (backend/brain/integrity.py): raised only after the candidate has looked away
 * from the screen several times within a minute while answering. On screen only, never spoken.
 */
export interface IntegrityFlagEvent {
  ts: IsoDateTime
  /** The claim, e.g. "You have looked away from me 3 times in under a minute. I suspect you of cheating." */
  message: string
  /** Shown as "Please <command>." */
  command: string
  /** Look-aways while answering, this session, when it was raised. */
  look_aways: number
}

/**
 * What Presage is doing, from the hidden sensor (sensing_client/presage_node), every few seconds:
 *   idle (no interview) · starting · warming_up (face found, no pulse yet) · measuring
 *   adjust (framing / lighting / motion / weak signal, see message) · error (camera access, API key, network)
 */
export type SensorState = 'idle' | 'starting' | 'warming_up' | 'measuring' | 'adjust' | 'error'

export interface SensorStatusEvent {
  state: SensorState
  /** What to tell the user, e.g. "Hold still." Null while measuring normally. */
  message: string | null
  /** True when the sensor runs with --simulate: the vitals are artificial. */
  simulated: boolean
}

export interface SessionEndedEvent {
  report_url: string
  replay_url: string
}

/** Server → client. */
export type ServerEvent =
  | { type: 'state'; data: { state: SessionState } }
  | { type: 'question'; data: QuestionEvent }
  | { type: 'filler'; data: FillerEvent }
  | { type: 'vitals'; data: VitalsEvent }
  | { type: 'coach_flag'; data: CoachFlagEvent }
  | { type: 'integrity_flag'; data: IntegrityFlagEvent }
  | { type: 'sensor'; data: SensorStatusEvent }
  | { type: 'session_ended'; data: SessionEndedEvent }

export type ServerEventType = ServerEvent['type']

/** Client → server. */
export type ClientEvent =
  | { type: 'answer_started'; data: { question_id: string } }
  | { type: 'answer_done'; data: { question_id: string; transcript: string } }
  /** The candidate looked away from the screen for a while (hooks/useLookAway.ts). */
  | { type: 'look_away'; data: { question_id: string } }
  | { type: 'end_session'; data: Record<string, never> }
