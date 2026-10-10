"""Brain-owned models: Gemini response schemas and the report shape served at /report."""

from typing import Literal, Optional

from pydantic import BaseModel

Action = Literal["follow_up", "ease_off", "push", "next", "wrap_up"]


# ---------------------------------------------------------------- Gemini call 1: plan_interview

class PlannedQuestion(BaseModel):
    text: str          # spoken aloud, max ~30 words
    topic: str
    difficulty: int    # 1 to 5


class InterviewPlan(BaseModel):   # response_schema wrapper (Gemini wants an object at the top level)
    questions: list[PlannedQuestion]


# ---------------------------------------------------------------- Gemini call 2: decide_next

class Decision(BaseModel):
    action: Action
    spoken_text: str     # what the interviewer says next (empty if action == "next")
    answer_quality: int  # 1 to 5, Gemini's read of the transcript
    rationale: str       # for logs and the report, never spoken


# ---------------------------------------------------------------- Gemini call 3: final_report

class QuestionFeedback(BaseModel):
    question_id: str
    what_happened: str   # 1-2 sentences about the answer content
    body_signal: str     # MUST cite numbers, e.g. "HR jumped 22% above baseline 8s in"
    advice: str          # one concrete thing to do next time


class ReportDraft(BaseModel):     # exactly what Gemini writes
    composure_score: int          # 0 to 100, higher is calmer
    summary: str                  # 2-3 sentences
    hardest_moment: str           # the single biggest spike, with numbers
    per_question: list[QuestionFeedback]
    top_tips: list[str]           # exactly 3


class CoachFlag(BaseModel):
    ts: str                       # ISO UTC, same value as the live coach_flag event
    question_id: Optional[str]
    level: Literal["rising", "high"]
    stress: float
    message: str


class Adaptation(BaseModel):
    question_id: str              # the question whose answer triggered the decision
    action: Action                # what the interviewer actually did next (after rule enforcement)
    answer_quality: int
    rationale: str


class Report(ReportDraft):
    """Served by GET /api/sessions/{id}/report. The brain.md fields plus additive extras."""
    coach_flags: list[CoachFlag] = []    # for red dots on the replay chart
    adaptations: list[Adaptation] = []   # where the interviewer eased off / pushed / followed up
    generated_by: Literal["gemini", "fallback", "fake"] = "gemini"
