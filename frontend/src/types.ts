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
  /** 0.0 to 1.0 */
  focus?: number | null
  expression?: Expression | null
  /** 0 to 1, face/signal quality. Data ignores readings under 0.5. */
  confidence: number
}

export interface VitalsBatch {
  readings: VitalsReading[]
}

export interface Baseline {
  heart_rate: number
  breathing_rate: number
  focus: number
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
  focus_mean: number | null
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
  focus_mean: number | null
  stress_mean: number | null
  stress_peak: number | null
  /** Seconds into the answer when stress peaked. */
  peak_offset_s: number | null
}

// ---------------------------------------------------------------------------
// REST: POST /api/sessions, GET /api/sessions/active
// ---------------------------------------------------------------------------

/**
 * Interview types offered on the setup page. PLAN.md does not pin the wire values;
 * `demo` matches brain.md ("interview_type="demo" ... 3 main questions").
 * The other slugs still need a thumbs-up from Brain.
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

/** `null` when no session is active. */
export type ActiveSessionResponse = { session_id: string } | null

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
  baseline: { heart_rate: number; breathing_rate: number; focus: number } | null
}

/** One 5-second bucket from the `vitals_5s` continuous aggregate. */
export interface ReplayPoint {
  t: IsoDateTime
  hr: number | null
  br: number | null
  focus: number | null
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
}

// ---------------------------------------------------------------------------
// WebSocket /ws/{session_id}: every message is {"type": ..., "data": {...}}
// ---------------------------------------------------------------------------

export type SessionState = 'calibrating' | 'asking' | 'listening' | 'thinking' | 'done'

export interface QuestionEvent {
  question_id: string
  /** 0 for calibration; follow-ups also increment it, so it can exceed `total`. */
  index: number
  total: number
  kind: QuestionKind
  text: string
  /** Path like "/audio/ab12cd34.mp3"; resolve with apiUrl() before playing. */
  audio_url: string
}

export interface FillerEvent {
  audio_url: string
}

/** About one per second. Every metric is null while the face is lost. */
export interface VitalsEvent {
  ts: IsoDateTime
  heart_rate: number | null
  breathing_rate: number | null
  focus: number | null
  /** Null until the baseline exists, or when confidence < 0.5. */
  stress: number | null
}

export type CoachLevel = 'rising' | 'high'

export interface CoachFlagEvent {
  ts: IsoDateTime
  level: CoachLevel
  /** On-screen only, e.g. "Heart rate up 22% from your baseline. Slow down." */
  message: string
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
  | { type: 'session_ended'; data: SessionEndedEvent }

export type ServerEventType = ServerEvent['type']

/** Client → server. */
export type ClientEvent =
  | { type: 'answer_started'; data: { question_id: string } }
  | { type: 'answer_done'; data: { question_id: string; transcript: string } }
  | { type: 'end_session'; data: Record<string, never> }
