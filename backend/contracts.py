"""Shared types for every role. Copied exactly from docs/PLAN.md section 3.

Frozen: do not change without telling the whole team first.

There is no `focus` vital: Presage never measures it, so it was removed everywhere (2026-10-11). A client
that still sends `focus` is fine: pydantic ignores unknown fields.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

Expression = Literal["neutral", "happy", "surprised", "frown", "confused", "unknown"]


class VitalsReading(BaseModel):
    session_id: Optional[str] = None   # if missing, backend uses the active session
    ts: datetime                       # UTC, stamped by the sensing client
    heart_rate: Optional[float] = None       # bpm
    breathing_rate: Optional[float] = None   # breaths per minute
    expression: Optional[Expression] = None
    confidence: float = 1.0                  # 0 to 1, face/signal quality


class VitalsBatch(BaseModel):
    readings: list[VitalsReading]


class Baseline(BaseModel):
    heart_rate: float
    breathing_rate: float
    n_samples: int


class WindowStats(BaseModel):          # "how is the person right now"
    seconds: int
    n_samples: int
    hr_mean: Optional[float]
    hr_delta_pct: Optional[float]      # % above/below baseline
    br_mean: Optional[float]
    br_delta_pct: Optional[float]
    dominant_expression: Optional[str]
    stress: Optional[float]            # 0 to 100


class QuestionStats(BaseModel):        # "how did they react to this question"
    question_id: str
    duration_s: float
    n_samples: int
    hr_mean: Optional[float]
    hr_peak: Optional[float]
    hr_delta_pct: Optional[float]
    br_mean: Optional[float]
    stress_mean: Optional[float]
    stress_peak: Optional[float]
    peak_offset_s: Optional[float]     # seconds into the answer when stress peaked
