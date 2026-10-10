"""STUB. Owned by Data. Replace freely with the real Tiger Data implementation.

Brain pushed this in-memory version so the interview loop runs end to end before the database
exists. Every function has the exact signature from docs/PLAN.md section 3 and returns correctly
shaped values computed from whatever vitals were inserted (so mock vitals produce real-looking
stress). Nothing persists across restarts.

Swap rule: keep the signatures, replace the bodies. Brain imports this module as
`from backend.data import queries as data` and calls nothing else.
"""

import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.contracts import Baseline, QuestionStats, VitalsReading, WindowStats

log = logging.getLogger("data.stub")

_sessions: dict[str, dict] = {}
_questions: dict[str, dict] = {}
_vitals: dict[str, list[dict]] = defaultdict(list)

WARMUP_S = 10
MIN_BASELINE_SAMPLES = 10
DEFAULT_BASELINE = Baseline(heart_rate=75.0, breathing_rate=15.0, focus=0.75, n_samples=0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _stress(row: dict, b: Optional[Baseline]) -> Optional[float]:
    """Inline copy of the PLAN.md formula. The real one lives in data/metrics.py."""
    if b is None or row.get("confidence", 0) < 0.5:
        return None
    parts = []
    if row.get("heart_rate") is not None and b.heart_rate:
        parts.append((0.5, _clamp((row["heart_rate"] - b.heart_rate) / b.heart_rate * 100 / 25)))
    if row.get("breathing_rate") is not None and b.breathing_rate:
        parts.append((0.3, _clamp((row["breathing_rate"] - b.breathing_rate) / b.breathing_rate * 100 / 40)))
    if row.get("focus") is not None:
        parts.append((0.2, _clamp((b.focus - row["focus"]) / 0.5)))
    if not parts:
        return None
    total_w = sum(w for w, _ in parts)
    return round(100 * sum(w * p for w, p in parts) / total_w, 1)


def _delta_pct(value: Optional[float], base: Optional[float]) -> Optional[float]:
    if value is None or not base:
        return None
    return round((value - base) / base * 100, 1)


# ---------------------------------------------------------------- sessions

async def create_session(user_name, interview_type, role_title) -> str:
    sid = str(uuid.uuid4())
    _sessions[sid] = {
        "id": sid, "user_name": user_name, "interview_type": interview_type,
        "role_title": role_title, "started_at": _now(), "ended_at": None,
        "baseline": None, "report": None,
    }
    return sid


async def get_active_session() -> Optional[str]:
    cutoff = _now() - timedelta(minutes=30)
    live = [s for s in _sessions.values() if s["ended_at"] is None and s["started_at"] > cutoff]
    return max(live, key=lambda s: s["started_at"])["id"] if live else None


async def end_session(session_id) -> None:
    if session_id in _sessions:
        _sessions[session_id]["ended_at"] = _now()


# ---------------------------------------------------------------- vitals

async def insert_vitals(readings: list[VitalsReading]) -> list[dict]:
    active = None
    rows = []
    for r in readings:
        sid = r.session_id
        if not sid:
            active = active or await get_active_session()
            sid = active
        if not sid or sid not in _sessions:
            continue
        row = {
            "time": _utc(r.ts), "session_id": sid, "heart_rate": r.heart_rate,
            "breathing_rate": r.breathing_rate, "focus": r.focus,
            "expression": r.expression, "confidence": r.confidence,
        }
        row["stress"] = _stress(row, _sessions[sid]["baseline"])
        _vitals[sid].append(row)
        rows.append(row)
    return rows


async def compute_baseline(session_id) -> Baseline:
    s = _sessions[session_id]
    start = s["started_at"] + timedelta(seconds=WARMUP_S)
    good = [v for v in _vitals[session_id] if v["time"] >= start and v["confidence"] >= 0.5]
    if len(good) < MIN_BASELINE_SAMPLES:
        log.warning("baseline: only %d samples for %s, using population defaults", len(good), session_id)
        b = DEFAULT_BASELINE
    else:
        b = Baseline(
            heart_rate=_mean(v["heart_rate"] for v in good) or DEFAULT_BASELINE.heart_rate,
            breathing_rate=_mean(v["breathing_rate"] for v in good) or DEFAULT_BASELINE.breathing_rate,
            focus=_mean(v["focus"] for v in good) or DEFAULT_BASELINE.focus,
            n_samples=len(good),
        )
    s["baseline"] = b
    for v in _vitals[session_id]:   # backfill so the chart isn't empty at the start
        v["stress"] = _stress(v, b)
    return b


async def get_baseline(session_id) -> Optional[Baseline]:
    s = _sessions.get(session_id)
    return s["baseline"] if s else None


async def get_window_stats(session_id, seconds=15) -> WindowStats:
    cutoff = _now() - timedelta(seconds=seconds)
    rows = [v for v in _vitals.get(session_id, []) if v["time"] > cutoff and v["confidence"] >= 0.5]
    b = await get_baseline(session_id)
    hr = _mean(v["heart_rate"] for v in rows)
    br = _mean(v["breathing_rate"] for v in rows)
    exprs = Counter(v["expression"] for v in rows if v["expression"])
    stress = _mean(v["stress"] for v in rows)
    return WindowStats(
        seconds=seconds, n_samples=len(rows),
        hr_mean=hr, hr_delta_pct=_delta_pct(hr, b.heart_rate if b else None),
        br_mean=br, br_delta_pct=_delta_pct(br, b.breathing_rate if b else None),
        focus_mean=_mean(v["focus"] for v in rows),
        dominant_expression=exprs.most_common(1)[0][0] if exprs else None,
        stress=round(stress, 1) if stress is not None else None,
    )


# ---------------------------------------------------------------- questions

async def insert_question(session_id, idx, kind, text) -> str:
    qid = str(uuid.uuid4())
    _questions[qid] = {
        "id": qid, "session_id": session_id, "idx": idx, "kind": kind, "text": text,
        "transcript": None, "created_at": _now(), "answer_started_at": None, "answered_at": None,
    }
    return qid


async def mark_answer_started(question_id, ts) -> None:
    if question_id in _questions:
        _questions[question_id]["answer_started_at"] = _utc(ts)


async def mark_answered(question_id, ts, transcript) -> None:
    if question_id in _questions:
        _questions[question_id]["answered_at"] = _utc(ts)
        _questions[question_id]["transcript"] = transcript


async def get_question_stats(question_id) -> QuestionStats:
    q = _questions[question_id]
    start = q["answer_started_at"] or q["created_at"]
    end = q["answered_at"] or _now()
    rows = [v for v in _vitals.get(q["session_id"], [])
            if start <= v["time"] <= end and v["confidence"] >= 0.5]
    b = await get_baseline(q["session_id"])
    hr = _mean(v["heart_rate"] for v in rows)
    hrs = [v["heart_rate"] for v in rows if v["heart_rate"] is not None]
    stressed = [v for v in rows if v["stress"] is not None]
    peak = max(stressed, key=lambda v: v["stress"]) if stressed else None
    stress_mean = _mean(v["stress"] for v in rows)
    return QuestionStats(
        question_id=question_id,
        duration_s=round((end - start).total_seconds(), 1),
        n_samples=len(rows),
        hr_mean=hr, hr_peak=max(hrs) if hrs else None,
        hr_delta_pct=_delta_pct(hr, b.heart_rate if b else None),
        br_mean=_mean(v["breathing_rate"] for v in rows),
        focus_mean=_mean(v["focus"] for v in rows),
        stress_mean=round(stress_mean, 1) if stress_mean is not None else None,
        stress_peak=peak["stress"] if peak else None,
        peak_offset_s=round((peak["time"] - start).total_seconds(), 1) if peak else None,
    )


async def get_session_history(session_id) -> list[dict]:
    qs = sorted((q for q in _questions.values() if q["session_id"] == session_id),
                key=lambda q: (q["idx"], q["created_at"]))
    return [
        {"question_id": q["id"], "idx": q["idx"], "kind": q["kind"], "text": q["text"],
         "transcript": q["transcript"], "stats": await get_question_stats(q["id"])}
        for q in qs
    ]


# ---------------------------------------------------------------- report

async def save_report(session_id, report: dict) -> None:
    if session_id in _sessions:
        _sessions[session_id]["report"] = report


async def get_report(session_id) -> Optional[dict]:
    s = _sessions.get(session_id)
    return s["report"] if s else None
