"""Every function Brain and Sensing call on Data (PLAN.md section 3).

The signatures here are a promise to the team: same names, same parameters,
same return types. Do not rename or reshape them without telling everyone.

STUB VERSION: returns fake but correctly shaped values from in-memory state so
Brain and Sensing can code against it before Tiger Data is wired up. The real
SQL replaces these bodies later with no signature changes.
"""
import random
import uuid
from datetime import datetime, timezone

from ._contracts import Baseline, QuestionStats, VitalsReading, WindowStats

_sessions: dict[str, dict] = {}     # session_id -> {started_at, ended_at, baseline, report, ...}
_questions: dict[str, dict] = {}    # question_id -> {session_id, idx, kind, text, transcript, ...}

_BASE_HR, _BASE_BR, _BASE_FOCUS = 72.0, 14.0, 0.8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fake_stress() -> float:
    return round(random.uniform(20, 60), 1)


async def create_session(user_name: str, interview_type: str, role_title: str) -> str:
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {"user_name": user_name, "interview_type": interview_type,
                             "role_title": role_title, "started_at": _now(), "ended_at": None,
                             "baseline": None, "report": None}
    return session_id


async def get_active_session() -> str | None:
    live = [(s["started_at"], sid) for sid, s in _sessions.items() if s["ended_at"] is None]
    return max(live)[1] if live else None


async def end_session(session_id: str) -> None:
    if session_id in _sessions:
        _sessions[session_id]["ended_at"] = _now()


async def insert_vitals(readings: list[VitalsReading]) -> list[dict]:
    """Returns one dict per stored row (keys match the vitals table) for broadcasting."""
    active = await get_active_session()
    rows = []
    for r in readings:
        session_id = r.session_id or active
        if session_id is None:
            continue                     # no active session: drop, same as the real version
        has_signal = r.confidence >= 0.5 and r.heart_rate is not None
        rows.append({"time": r.ts, "session_id": session_id, "heart_rate": r.heart_rate,
                     "breathing_rate": r.breathing_rate, "focus": r.focus,
                     "expression": r.expression, "confidence": r.confidence,
                     "stress": _fake_stress() if has_signal else None})
    return rows


async def compute_baseline(session_id: str) -> Baseline:
    baseline = Baseline(heart_rate=round(random.uniform(68, 76), 1),
                        breathing_rate=round(random.uniform(13, 15), 1),
                        focus=round(random.uniform(0.75, 0.85), 2), n_samples=30)
    _sessions.setdefault(session_id, {"started_at": _now(), "ended_at": None,
                                      "baseline": None, "report": None})["baseline"] = baseline
    return baseline


async def get_baseline(session_id: str) -> Baseline | None:
    return _sessions.get(session_id, {}).get("baseline")


async def get_window_stats(session_id: str, seconds: int = 15) -> WindowStats:
    base = await get_baseline(session_id) or Baseline(heart_rate=_BASE_HR, breathing_rate=_BASE_BR,
                                                        focus=_BASE_FOCUS, n_samples=0)
    hr = round(base.heart_rate * random.uniform(1.0, 1.2), 1)
    br = round(base.breathing_rate * random.uniform(1.0, 1.25), 1)
    return WindowStats(seconds=seconds, n_samples=seconds,
                       hr_mean=hr, hr_delta_pct=round(100 * (hr / base.heart_rate - 1), 1),
                       br_mean=br, br_delta_pct=round(100 * (br / base.breathing_rate - 1), 1),
                       focus_mean=round(random.uniform(0.5, 0.85), 2),
                       dominant_expression=random.choice(["neutral", "neutral", "frown"]),
                       stress=_fake_stress())


async def insert_question(session_id: str, idx: int, kind: str, text: str) -> str:
    question_id = str(uuid.uuid4())
    _questions[question_id] = {"session_id": session_id, "idx": idx, "kind": kind, "text": text,
                               "transcript": None, "created_at": _now(),
                               "answer_started_at": None, "answered_at": None}
    return question_id


async def mark_answer_started(question_id: str, ts: datetime) -> None:
    if question_id in _questions:
        _questions[question_id]["answer_started_at"] = ts


async def mark_answered(question_id: str, ts: datetime, transcript: str) -> None:
    if question_id in _questions:
        _questions[question_id].update(answered_at=ts, transcript=transcript)


async def get_question_stats(question_id: str) -> QuestionStats:
    duration = round(random.uniform(20, 60), 1)
    hr_mean = round(_BASE_HR * random.uniform(1.0, 1.2), 1)
    stress_mean = _fake_stress()
    return QuestionStats(question_id=question_id, duration_s=duration, n_samples=int(duration),
                         hr_mean=hr_mean, hr_peak=round(hr_mean + random.uniform(2, 10), 1),
                         hr_delta_pct=round(100 * (hr_mean / _BASE_HR - 1), 1),
                         br_mean=round(_BASE_BR * random.uniform(1.0, 1.25), 1),
                         focus_mean=round(random.uniform(0.5, 0.85), 2),
                         stress_mean=stress_mean,
                         stress_peak=round(min(100.0, stress_mean + random.uniform(5, 30)), 1),
                         peak_offset_s=round(random.uniform(0, duration), 1))


async def get_session_history(session_id: str) -> list[dict]:
    """[{idx, kind, text, transcript, stats: QuestionStats}] ordered by idx, for Gemini."""
    qs = sorted(((q["idx"], qid, q) for qid, q in _questions.items()
                 if q["session_id"] == session_id), key=lambda t: t[0])
    return [{"idx": q["idx"], "kind": q["kind"], "text": q["text"], "transcript": q["transcript"],
             "stats": await get_question_stats(qid)} for _, qid, q in qs]


async def save_report(session_id: str, report: dict) -> None:
    _sessions.setdefault(session_id, {"started_at": _now(), "ended_at": None,
                                      "baseline": None, "report": None})["report"] = report


async def get_report(session_id: str) -> dict | None:
    return _sessions.get(session_id, {}).get("report")
